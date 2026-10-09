import os
import re
import uuid
import json
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_file
import yt_dlp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, 'downloads')

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
        }
        res = requests.head(url, allow_redirects=True, headers=headers, timeout=5)
        return res.url
    except Exception:
        return url


def extraer_tiktok_api(url, formato):
    """Extrae enlaces de TikTok mediante TikWM."""
    try:
        api_url = f"https://www.tikwm.com/api/?url={url}"
        res = requests.get(api_url, timeout=10).json()
        
        if res.get('code') == 0 and 'data' in res:
            data = res['data']
            if formato == 'mp3' and data.get('music'):
                return True, data.get('music'), 'mp3', True
            elif data.get('play'):
                return True, data.get('play'), 'mp4', True
            elif 'images' in data and len(data['images']) > 0:
                return True, data['images'][0], 'jpg', True
    except Exception as e:
        print(f"Error TikWM API: {e}")
    return False, None, None, False


def extraer_pinterest_directo(url):
    """Extrae enlaces directos de Pinterest."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
    }
    try:
        res = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
        
        mp4_urls = re.findall(r'https://v1\.pinimg\.com/videos/[^\s"\'<>]+\.mp4', res.text)
        if mp4_urls:
            return mp4_urls[0], 'mp4'

        soup = BeautifulSoup(res.text, 'html.parser')
        og_video = soup.find('meta', property='og:video') or soup.find('meta', property='og:video:secure_url')
        if og_video and og_video.get('content'):
            return og_video['content'], 'mp4'

        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            img_url = og_image['content']
            img_url_hd = re.sub(r'/(x\d+|originals|\d+x)/', '/originals/', img_url)
            ext = 'png' if '.png' in img_url.lower() else ('webp' if '.webp' in img_url.lower() else 'jpg')
            return img_url_hd, ext
    except Exception:
        pass
    return None, None


def extraer_youtube(url, formato):
    """Extrae YouTube rotando instancias de Cobalt para evadir el bloqueo de IP de Render."""
    
    # Lista de instancias de API públicas de respaldo
    instancias = [
        "https://api.cobalt.tools",
        "https://cobalt-api.kwi.li",
        "https://co.wuk.sh"
    ]

    payload = {
        "url": url,
        "downloadMode": "audio" if formato == 'mp3' else "auto",
        "audioFormat": "mp3" if formato == 'mp3' else "best"
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    # 1. Probar instancias públicas de Cobalt
    for base_url in instancias:
        try:
            res = requests.post(f"{base_url}/", json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                media_url = data.get('url') or (data.get('picker') and data['picker'][0].get('url'))
                if media_url:
                    ext = 'mp3' if formato == 'mp3' else 'mp4'
                    return True, media_url, ext, True
        except Exception as e:
            print(f"Fallo en instancia {base_url}: {e}")
            continue

    # 2. Intento de fallback con Invidious API
    try:
        video_id_match = re.search(r"(?:v=|\/([0-9A-Za-z_-]{11}))", url)
        if video_id_match:
            v_id = video_id_match.group(1) or video_id_match.group(0).replace("v=", "")
            inv_res = requests.get(f"https://inv.tux.pizza/api/v1/videos/{v_id}", timeout=8).json()
            
            if formato == 'mp3' and 'adaptiveFormats' in inv_res:
                for f in inv_res['adaptiveFormats']:
                    if 'audio' in f.get('type', ''):
                        return True, f['url'], 'mp3', True
            elif 'formatStreams' in inv_res and len(inv_res['formatStreams']) > 0:
                return True, inv_res['formatStreams'][-1]['url'], 'mp4', True
    except Exception as e:
        print(f"Fallo en Invidious API: {e}")

    return False, None, None, False

@app.route('/descargar', methods=['POST'])
def descargar():
    try:
        data = request.get_json() or {}
        entrada = data.get('url', '').strip()
        formato = data.get('formato', 'mp4')
        plataforma = data.get('plataforma', '').lower()

        if not entrada:
            return jsonify({"error": "Por favor, pega un enlace válido."}), 400

        urls = re.findall(r'https?://[^\s"]+', entrada)
        link_bruto = urls[0] if urls else entrada

        link = resolver_url_final(link_bruto)
        
        exito = False
        media_url = None
        ext = formato

        # 1. TIKTOK
        if 'tiktok.com' in link or 'vt.tiktok.com' in link_bruto or plataforma == 'tiktok':
            exito, media_url, ext, es_directo = extraer_tiktok_api(link, formato)

        # 2. PINTEREST
        if not exito and ('pinterest.com' in link or 'pin.it' in link_bruto or plataforma == 'pinterest'):
            p_url, p_ext = extraer_pinterest_directo(link)
            if p_url:
                exito = True
                media_url = p_url
                ext = p_ext

        # 3. YOUTUBE
        if not exito:
            exito, media_url, ext, es_directo = extraer_youtube(link, formato)

        if exito and media_url:
            return jsonify({
                "success": True,
                "download_url": media_url,
                "filename": f"media.{ext}",
                "tipo": ext
            })
        else:
            return jsonify({
                "error": "No se pudo obtener el archivo de este enlace. Intenta con otro video público."
            }), 400

    except Exception as err:
        print(f"Error interno del servidor: {err}")
        return jsonify({"error": "Ocurrió un error interno en el servidor."}), 500


@app.route('/sw.js')
def serve_sw():
    sw_path = os.path.join(BASE_DIR, 'templates', 'sw.js')
    if not os.path.exists(sw_path):
        sw_path = os.path.join(BASE_DIR, 'sw.js')
    return send_file(sw_path, mimetype='application/javascript')


if __name__ == '__main__':
    app.run(debug=True, port=5000)
