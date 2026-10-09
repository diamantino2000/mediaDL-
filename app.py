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

# --- RUTAS DE NAVEGACIÓN Y MULTI-PLATAFORMA ---
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
    """Sigue las redirecciones automáticas de acortadores (pin.it, vt.tiktok.com, etc.)."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
        }
        res = requests.head(url, allow_redirects=True, headers=headers, timeout=5)
        return res.url
    except Exception:
        return url


def extraer_tiktok_api(url, formato, filepath_base):
    """Soporte directo para vídeos, MP3 e imágenes/carruseles de TikTok usando TikWM."""
    try:
        api_url = f"https://www.tikwm.com/api/?url={url}"
        res = requests.get(api_url, timeout=10).json()
        
        if res.get('code') == 0 and 'data' in res:
            data = res['data']
            media_url = None
            extension = 'mp4'

            # 1. Extraer Audio MP3
            if formato == 'mp3':
                media_url = data.get('music')
                extension = 'mp3'
            # 2. Extraer Vídeo MP4
            elif data.get('play'):
                media_url = data.get('play')
                extension = 'mp4'
            # 3. Extraer Imagen/Carrusel (enlace tipo /photo/)
            elif 'images' in data and len(data['images']) > 0:
                media_url = data['images'][0]
                extension = 'jpg'

            if media_url:
                final_filename = f"{os.path.basename(filepath_base)}.{extension}"
                final_filepath = f"{filepath_base}.{extension}"
                r = requests.get(media_url, timeout=20)
                if r.status_code == 200:
                    with open(final_filepath, 'wb') as f:
                        f.write(r.content)
                    return True, final_filename, extension
    except Exception as e:
        print(f"Error TikWM API: {e}")
    return False, None, None


def extraer_pinterest_directo(url):
    """Extrae MP4 reales o imágenes HD de Pinterest mediante Web Scraping."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
    }
    try:
        res = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
        
        # CDN directo de Pinterest
        mp4_urls = re.findall(r'https://v1\.pinimg\.com/videos/[^\s"\'<>]+\.mp4', res.text)
        if not mp4_urls:
            mp4_urls = re.findall(r'https://[^\s"\'<>]+\.mp4', res.text)
            
        if mp4_urls:
            for video_url in mp4_urls:
                if 'pinterest' in video_url or 'pinimg' in video_url:
                    return video_url, 'mp4'

        soup = BeautifulSoup(res.text, 'html.parser')
        og_video = soup.find('meta', property='og:video') or soup.find('meta', property='og:video:secure_url')
        if og_video and og_video.get('content'):
            return og_video['content'], 'mp4'

        video_tag = soup.find('video')
        if video_tag and video_tag.get('src'):
            return video_tag['src'], 'mp4'

        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            img_url = og_image['content']
            img_url_hd = re.sub(r'/(x\d+|originals|\d+x)/', '/originals/', img_url)
            ext = 'png' if '.png' in img_url.lower() else ('webp' if '.webp' in img_url.lower() else 'jpg')
            return img_url_hd, ext

    except Exception:
        pass
    return None, None


def descargar_youtube_api(url, formato, filepath_base):
    """Fallback usando la API pública de Cobalt para evitar bloqueos de IP de YouTube en Render."""
    try:
        # Petición a instancia pública de cobalt.tools
        api_url = "https://api.cobalt.tools/api/json"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        payload = {
            "url": url,
            "isAudioOnly": True if formato == 'mp3' else False,
            "aFormat": "mp3" if formato == 'mp3' else None
        }

        res = requests.post(api_url, json=payload, headers=headers, timeout=15).json()

        media_url = None
        if res.get('status') == 'redirect' or res.get('status') == 'stream':
            media_url = res.get('url')

        if media_url:
            extension = 'mp3' if formato == 'mp3' else 'mp4'
            final_filename = f"{os.path.basename(filepath_base)}.{extension}"
            final_filepath = f"{filepath_base}.{extension}"

            r = requests.get(media_url, timeout=30, stream=True)
            if r.status_code == 200:
                with open(final_filepath, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                return True, final_filename, extension
    except Exception as e:
        print(f"Error en API YouTube (Cobalt): {e}")

    # Si la API falla, intenta con yt-dlp nativo
    return descargar_con_ytdlp_nativo(url, formato, filepath_base)


def descargar_con_ytdlp_nativo(url, formato, filepath_sin_ext):
    """Intento secundario con yt_dlp pasando Cookies / User-Agent rotativos."""
    extension = 'mp3' if formato == 'mp3' else 'mp4'
    outtmpl = f"{filepath_sin_ext}.%(ext)s"
    
    ydl_opts = {
        'format': 'bestaudio/best' if formato == 'mp3' else 'b[ext=mp4]/best',
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'mweb', 'android_vr']
            }
        }
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        archivo_final = f"{filepath_sin_ext}.{extension}"
        if os.path.exists(archivo_final) and os.path.getsize(archivo_final) > 0:
            return True, os.path.basename(archivo_final), extension
    except Exception as e:
        print(f"Error yt_dlp nativo: {e}")

    return False, None, None


@app.route('/descargar', methods=['POST'])
def descargar():
    data = request.get_json() or {}
    entrada = data.get('url', '').strip()
    formato = data.get('formato', 'mp4')
    plataforma = data.get('plataforma', '').lower()

    if not entrada:
        return jsonify({"error": "Por favor, pega un enlace válido."}), 400

    urls = re.findall(r'https?://[^\s"]+', entrada)
    link_bruto = urls[0] if urls else entrada

    link = resolver_url_final(link_bruto)
    id_unico = str(uuid.uuid4())[:8]

    descarga_exitosa = False
    filename = ""
    filepath_base = os.path.join(DOWNLOAD_FOLDER, f"media_{id_unico}")
    tipo_retorno = formato

    # --- 1. PROCESAR TIKTOK ---
    if 'tiktok.com' in link or 'vt.tiktok.com' in link_bruto or plataforma == 'tiktok':
        exito, archivo_gen, ext_gen = extraer_tiktok_api(link, formato, filepath_base)
        if exito:
            descarga_exitosa = True
            filename = archivo_gen
            tipo_retorno = ext_gen

    # --- 2. PROCESAR PINTEREST ---
    if not descarga_exitosa and ('pinterest.com' in link or 'pin.it' in link_bruto or plataforma == 'pinterest'):
        media_url, ext = extraer_pinterest_directo(link)
        if media_url:
            filename = f"media_{id_unico}.{ext}"
            filepath = os.path.join(DOWNLOAD_FOLDER, filename)
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
                req = requests.get(media_url, headers=headers, timeout=20)
                if req.status_code == 200:
                    with open(filepath, 'wb') as f:
                        f.write(req.content)
                    descarga_exitosa = True
                    tipo_retorno = ext
            except Exception:
                descarga_exitosa = False

    # --- 3. PROCESAR YOUTUBE / EXTRACTOR GENERAL (YT-DLP) ---
    if not descarga_exitosa:
        exito, archivo_gen, ext_gen = descargar_con_ytdlp(link, formato, filepath_base)
        if exito:
            descarga_exitosa = True
            filename = archivo_gen
            tipo_retorno = ext_gen

    # --- RESPUESTA ---
    if descarga_exitosa and filename:
        return jsonify({
            "success": True,
            "download_url": f"/obtener-archivo/{filename}",
            "filename": filename,
            "tipo": tipo_retorno
        })
    else:
        return jsonify({
            "error": "No se pudo extraer el archivo. Verifica que el enlace sea público."
        }), 500


@app.route('/obtener-archivo/<filename>')
def obtener_archivo(filename):
    filepath = os.path.join(DOWNLOAD_FOLDER, filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    return jsonify({"error": "El archivo solicitado no existe."}), 404


@app.route('/sw.js')
def serve_sw():
    sw_path = os.path.join(BASE_DIR, 'templates', 'sw.js')
    if not os.path.exists(sw_path):
        sw_path = os.path.join(BASE_DIR, 'sw.js')
    return send_file(sw_path, mimetype='application/javascript')


if __name__ == '__main__':
    print("-------------------------------------------------------")
    print("🚀 Servidor listo en: http://127.0.0.1:5000/")
    print("-------------------------------------------------------")
    app.run(debug=True, port=5000)
