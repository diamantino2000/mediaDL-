import os
import re
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)

# --- RUTAS DE NAVEGACIÓN ---
@app.route('/')
@app.route('/tiktok')
@app.route('/youtube')
@app.route('/pinterest')
def index():
    html_raiz = os.path.join(BASE_DIR, 'index.html')
    html_templates = os.path.join(BASE_DIR, 'templates', 'index.html')

    if os.path.exists(html_raiz):
        return send_file(html_raiz)
    elif os.path.exists(html_templates):
        return send_file(html_templates)
    else:
        return "<h3>❌ No se encontró index.html</h3>", 404


@app.route('/favicon.ico')
def favicon():
    logo_path = os.path.join(BASE_DIR, 'static', 'logo.png')
    if os.path.exists(logo_path):
        return send_file(logo_path)
    return "", 204


def resolver_url_final(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        res = requests.head(url, allow_redirects=True, headers=headers, timeout=4)
        return res.url
    except Exception:
        return url

# --- TIKTOK ---
def extraer_tiktok(url, formato):
    try:
        res = requests.get(f"https://www.tikwm.com/api/?url={url}", timeout=5).json()
        if res.get('code') == 0 and 'data' in res:
            data = res['data']
            if formato == 'mp3' and data.get('music'):
                return True, data.get('music'), 'mp3'
            elif data.get('play'):
                return True, data.get('play'), 'mp4'
            elif 'images' in data and len(data['images']) > 0:
                return True, data['images'][0], 'jpg'
    except Exception:
        pass
    return False, None, None

# --- PINTEREST ---
def extraer_pinterest(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        res = requests.get(url, headers=headers, allow_redirects=True, timeout=5)
        mp4_urls = re.findall(r'https://v1\.pinimg\.com/videos/[^\s"\'<>]+\.mp4', res.text)
        if mp4_urls:
            return True, mp4_urls[0], 'mp4'

        soup = BeautifulSoup(res.text, 'html.parser')
        og_video = soup.find('meta', property='og:video') or soup.find('meta', property='og:video:secure_url')
        if og_video and og_video.get('content'):
            return True, og_video['content'], 'mp4'

        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            img_url = og_image['content']
            img_url_hd = re.sub(r'/(x\d+|originals|\d+x)/', '/originals/', img_url)
            ext = 'png' if '.png' in img_url.lower() else 'jpg'
            return True, img_url_hd, ext
    except Exception:
        pass
    return False, None, None

# --- YOUTUBE (ULTRARÁPIDO CON TIMEOUTS CORTOS) ---
def extraer_youtube(url, formato):
    # 1. Probar Cobalts rápidos
    instancias = ["https://api.cobalt.tools", "https://co.wuk.sh", "https://cobalt-api.kwi.li"]
    payload = {
        "url": url,
        "downloadMode": "audio" if formato == 'mp3' else "auto"
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    for base in instancias:
        try:
            r = requests.post(f"{base}/", json=payload, headers=headers, timeout=3)
            if r.status_code == 200:
                data = r.json()
                media_url = data.get('url') or (data.get('picker') and data['picker'][0].get('url'))
                if media_url:
                    return True, media_url, ('mp3' if formato == 'mp3' else 'mp4')
        except Exception:
            continue

    # 2. Probar Invidious rápido
    v_match = re.search(r"(?:v=|\/|shorts\/)([0-9A-Za-z_-]{11})", url)
    if v_match:
        v_id = v_match.group(1)
        nodes = ["https://inv.tux.pizza", "https://invidious.nerdvpn.de"]
        for node in nodes:
            try:
                inv = requests.get(f"{node}/api/v1/videos/{v_id}", timeout=3).json()
                if formato == 'mp3' and 'adaptiveFormats' in inv:
                    for f in inv['adaptiveFormats']:
                        if 'audio' in f.get('type', ''):
                            return True, f['url'], 'mp3'
                elif 'formatStreams' in inv and len(inv['formatStreams']) > 0:
                    return True, inv['formatStreams'][-1]['url'], 'mp4'
            except Exception:
                continue

    return False, None, None


@app.route('/descargar', methods=['POST'])
def descargar():
    try:
        data = request.get_json(force=True, silent=True) or {}
        entrada = data.get('url', '').strip()
        formato = data.get('formato', 'mp4')
        plataforma = data.get('plataforma', '').lower()

        if not entrada:
            return jsonify({"success": False, "error": "Pega una URL primero."}), 400

        urls = re.findall(r'https?://[^\s"]+', entrada)
        link_bruto = urls[0] if urls else entrada
        link = resolver_url_final(link_bruto)

        exito = False
        media_url = None
        ext = formato

        if 'tiktok.com' in link or 'vt.tiktok.com' in link_bruto or plataforma == 'tiktok':
            exito, media_url, ext = extraer_tiktok(link, formato)

        if not exito and ('pinterest.com' in link or 'pin.it' in link_bruto or plataforma == 'pinterest'):
            exito, media_url, ext = extraer_pinterest(link)

        if not exito:
            exito, media_url, ext = extraer_youtube(link, formato)

        if exito and media_url:
            return jsonify({
                "success": True,
                "download_url": media_url,
                "filename": f"media.{ext}",
                "tipo": ext
            })
        else:
            return jsonify({
                "success": False,
                "error": "El servidor de extracción tardó demasiado o la URL es privada. Reintenta."
            }), 200

    except Exception as err:
        return jsonify({"success": False, "error": f"Error interno: {str(err)}"}), 200


@app.route('/sw.js')
def serve_sw():
    sw_path = os.path.join(BASE_DIR, 'templates', 'sw.js')
    if not os.path.exists(sw_path):
        sw_path = os.path.join(BASE_DIR, 'sw.js')
    return send_file(sw_path, mimetype='application/javascript')


if __name__ == '__main__':
    app.run(debug=True, port=5000)
