import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

MAX_PROMPT_LENGTH = 3000
MAX_REQUEST_SIZE = 16000

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"

PINTAR_SYSTEM_PROMPT = """
Anda adalah Asisten AI PINTAR (Pusat Informasi dan Tracking Layanan Rakyat).

Tugas Anda membantu masyarakat memahami layanan publik melalui platform PINTAR.

Layanan utama:
1. Pengaduan infrastruktur dan fasilitas umum.
2. Perizinan dan surat administrasi kelurahan.
3. Verifikasi dan peninjauan bantuan sosial.
4. Informasi publik dan berita resmi.

Panduan:
- Gunakan bahasa Indonesia yang sopan, jelas, dan mudah dipahami.
- Berikan jawaban yang ringkas dan relevan.
- Jangan mengarang kebijakan atau peraturan pemerintah.
- Jangan mengarang status tiket atau data pribadi warga.
- Untuk pemeriksaan status laporan, arahkan warga ke menu Lacak Tiket.
- Jika tidak mengetahui jawaban pasti, sampaikan keterbatasan informasi.
- Jangan meminta warga mengirimkan NIK atau informasi rahasia melalui chat.
"""


class handler(BaseHTTPRequestHandler):
    def _add_cors_headers(self):
        origin = self.headers.get("Origin", "")
        configured = os.environ.get("ALLOWED_ORIGINS", "*")
        allowed_origins = [
            item.strip()
            for item in configured.split(",")
            if item.strip()
        ]

        if "*" in allowed_origins:
            self.send_header("Access-Control-Allow-Origin", "*")
        elif origin and origin in allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )

    def _send_json(self, status_code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")

        self.send_response(status_code)
        self._add_cors_headers()
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._add_cors_headers()
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")

        if path in ("/api/chat", ""):
            self._send_json(200, {
                "status": "ok",
                "service": "PINTAR AI Chatbot",
                "hasKey": bool(os.environ.get("GEMINI_API_KEY", ""))
            })
            return

        self._send_json(404, {
            "error": "Endpoint tidak ditemukan"
        })

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path != "/api/chat":
            self._send_json(404, {
                "error": "Endpoint tidak ditemukan"
            })
            return

        try:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except (TypeError, ValueError):
                content_length = 0

            if content_length <= 0:
                self._send_json(400, {
                    "error": "Request kosong"
                })
                return

            if content_length > MAX_REQUEST_SIZE:
                self._send_json(413, {
                    "error": "Ukuran request terlalu besar"
                })
                return

            raw_body = self.rfile.read(content_length)
            data = json.loads(raw_body.decode("utf-8"))

            if not isinstance(data, dict):
                self._send_json(400, {
                    "error": "Format request tidak valid"
                })
                return

            user_prompt = data.get("prompt", "")
            user_name = data.get("userName", "Warga")

            if not isinstance(user_prompt, str):
                self._send_json(400, {
                    "error": "Format pertanyaan tidak valid"
                })
                return

            if not isinstance(user_name, str):
                user_name = "Warga"

            user_prompt = user_prompt.strip()
            user_name = user_name.strip()[:80] or "Warga"

            if not user_prompt:
                self._send_json(400, {
                    "error": "Pertanyaan tidak boleh kosong"
                })
                return

            if len(user_prompt) > MAX_PROMPT_LENGTH:
                self._send_json(413, {
                    "error": "Pertanyaan terlalu panjang"
                })
                return

            api_key = os.environ.get("GEMINI_API_KEY", "")
            if api_key:
                reply = self._ask_gemini(user_prompt, user_name, api_key)
                if reply:
                    self._send_json(200, {
                        "reply": reply,
                        "source": "gemini_ai"
                    })
                    return

            reply = self._smart_knowledge_base(user_prompt, user_name)
            self._send_json(200, {
                "reply": reply,
                "source": "pintar_knowledge_base"
            })

        except json.JSONDecodeError:
            self._send_json(400, {
                "error": "Format JSON tidak valid"
            })
        except UnicodeDecodeError:
            self._send_json(400, {
                "error": "Format teks tidak valid"
            })
        except Exception as error:
            print("Server error:", error)
            self._send_json(500, {
                "error": "Terjadi kesalahan pada server"
            })

    def _ask_gemini(self, question, name, api_key):
        model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
        if not model:
            model = DEFAULT_GEMINI_MODEL

        url = (
            "https://generativelanguage.googleapis.com/"
            f"v1beta/models/{model}:generateContent"
        )

        payload = {
            "systemInstruction": {
                "parts": [{"text": PINTAR_SYSTEM_PROMPT}]
            },
            "contents": [{
                "role": "user",
                "parts": [{
                    "text": (
                        f"Nama pengguna: {name}\n"
                        f"Pertanyaan: {question}"
                    )
                }]
            }],
            "generationConfig": {
                "maxOutputTokens": 400
            }
        }

        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(request, timeout=24) as response:
                result = json.loads(response.read().decode("utf-8"))

            candidates = result.get("candidates", [])
            if not candidates:
                return None

            parts = candidates[0].get("content", {}).get("parts", [])
            answer = "".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, dict)
            ).strip()

            return answer or None

        except urllib.error.HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            print("Gemini HTTP error:", error.code, error_body[:1000])
            return None
        except Exception as error:
            print("Gemini connection error:", error)
            return None

    def _smart_knowledge_base(self, question, name):
        q = question.lower()

        if any(word in q for word in ["batalkan", "batal", "cancel"]):
            return (
                f"Halo {name}, Anda dapat membatalkan laporan "
                "melalui menu Lacak Tiket. Pembatalan hanya "
                "dapat dilakukan sesuai ketentuan status tiket."
            )

        if any(word in q for word in ["peta", "map", "lokasi", "koordinat"]):
            return (
                "Pada formulir Pengaduan Fasilitas Umum, "
                "Anda dapat memilih titik lokasi melalui peta "
                "atau menggunakan fitur GPS."
            )

        if any(word in q for word in ["kamera", "foto", "bukti"]):
            return (
                "Anda dapat melampirkan foto bukti melalui "
                "fitur unggah gambar pada formulir laporan."
            )

        if any(word in q for word in ["bansos", "bantuan sosial", "pkh", "bpnt", "blt"]):
            return (
                "Untuk peninjauan bantuan sosial, silakan "
                "pilih layanan Verifikasi Bansos pada menu "
                "Buat Pengaduan."
            )

        if any(word in q for word in ["berita", "informasi publik", "news"]):
            return (
                "Anda dapat melihat pengumuman dan perkembangan "
                "layanan melalui menu Informasi Publik."
            )

        if any(word in q for word in ["surat", "domisili", "sktm", "sku", "izin"]):
            return (
                "Untuk layanan administrasi, buka menu "
                "Buat Pengaduan kemudian pilih "
                "Perizinan dan Surat Administrasi."
            )

        return (
            f"Terima kasih, {name}. Saya asisten PINTAR. "
            "Saya dapat membantu pertanyaan seputar fasilitas "
            "umum, bansos, perizinan, tiket laporan, "
            "dan informasi publik."
        )
