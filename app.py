import os
import requests
from flask import Flask, request, Response, jsonify
from urllib.parse import urlparse

app = Flask(__name__)

# Simple, fast image proxy/downloader for Render free web service
# Usage: GET /?url=https://example.com/image.jpg
# or GET /download?url=https://example.com/image.jpg

ALLOWED_CONTENT_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/gif",
    "image/webp", "image/bmp", "image/tiff", "image/svg+xml",
    "image/x-icon", "image/vnd.microsoft.icon", "image/avif",
    "image/heic", "image/heif"
}

MAX_SIZE = 25 * 1024 * 1024  # 25 MB limit
TIMEOUT = 25  # seconds


@app.route("/", methods=["GET"])
@app.route("/download", methods=["GET"])
def download_image():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({
            "error": "Missing url parameter",
            "usage": "GET /?url=https://example.com/image.jpg"
        }), 400

    # Basic URL validation
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return jsonify({"error": "Invalid URL. Only http/https allowed."}), 400
    except Exception:
        return jsonify({"error": "Invalid URL"}), 400

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/*,*/*;q=0.8",
    }

    try:
        # Stream download to check size early
        with requests.get(url, headers=headers, timeout=TIMEOUT, stream=True, allow_redirects=True) as resp:
            if resp.status_code != 200:
                return jsonify({
                    "error": f"Failed to download. Status code: {resp.status_code}"
                }), 400

            content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()

            # If content-type not image, still try if URL looks like image, but prefer strict
            if content_type and not content_type.startswith("image/") and content_type not in ALLOWED_CONTENT_TYPES:
                # Some CDNs send wrong type; check extension as fallback
                path = parsed.path.lower()
                if not any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".svg", ".ico", ".avif", ".heic")):
                    return jsonify({
                        "error": f"URL does not point to an image. Content-Type: {content_type or 'unknown'}"
                    }), 400

            # Collect content with size limit
            content = b""
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    content += chunk
                    if len(content) > MAX_SIZE:
                        return jsonify({"error": "Image too large (max 25MB)"}), 400

            if not content:
                return jsonify({"error": "Empty response from URL"}), 400

            # Final content-type decision
            if not content_type or not content_type.startswith("image/"):
                # Guess from extension
                path = parsed.path.lower()
                if path.endswith((".jpg", ".jpeg")):
                    content_type = "image/jpeg"
                elif path.endswith(".png"):
                    content_type = "image/png"
                elif path.endswith(".gif"):
                    content_type = "image/gif"
                elif path.endswith(".webp"):
                    content_type = "image/webp"
                elif path.endswith(".bmp"):
                    content_type = "image/bmp"
                elif path.endswith(".svg"):
                    content_type = "image/svg+xml"
                else:
                    content_type = "image/jpeg"  # safe default

            # Return the image as-is
            return Response(
                content,
                mimetype=content_type,
                headers={
                    "Content-Length": str(len(content)),
                    "Cache-Control": "public, max-age=3600",
                    "Access-Control-Allow-Origin": "*",
                }
            )

    except requests.exceptions.Timeout:
        return jsonify({"error": "Download timed out"}), 408
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Download failed: {str(e)[:200]}"}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)[:200]}"}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
