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
    """Extrae información de YouTube utilizando cookies para evadir el bloqueo de IP."""
    cookies_path = os.path.join(BASE_DIR, 'cookies.txt')
    
    ydl_opts = {
        'format': 'bestaudio/best' if formato == 'mp3' else 'b[ext=mp4]/best[ext=mp4]/best',
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'mweb', 'android_vr']
            }
        }
    }

    # Si el archivo cookies.txt existe, se lo pasamos a yt-dlp
    if os.path.exists(cookies_path):
        ydl_opts['cookiefile'] = cookies_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'url' in info:
                ext = 'mp3' if formato == 'mp3' else info.get('ext', 'mp4')
                return True, info['url'], ext, True
            elif 'formats' in info and len(info['formats']) > 0:
                direct_url = info['formats'][-1]['url']
                return True, direct_url, 'mp4', True
    except Exception as e:
        print(f"Error yt_dlp: {e}")

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
