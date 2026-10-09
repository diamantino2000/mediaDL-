import os
import re
import uuid
import json
import subprocess
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_file

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
    """Fallback directo usando la API de TikWM para videos, MP3 e imágenes de TikTok."""
    try:
        api_url = f"https://www.tikwm.com/api/?url={url}"
        res = requests.get(api_url, timeout=10).json()
        
        if res.get('code') == 0 and 'data' in res:
            data = res['data']
            media_url = None
            extension = 'mp4'

            # 1. Solicitud de MP3
            if formato == 'mp3':
                media_url = data.get('music')
                extension = 'mp3'
            # 2. Publicación de Video
            elif data.get('play'):
                media_url = data.get('play')
                extension = 'mp4'
            # 3. Publicación de Fotos/Carrusel (Slide)
            elif 'images' in data and len(data['images']) > 0:
                media_url = data['images'][0]
                extension = 'jpg'

            if media_url:
                final_filepath = f"{filepath_base}.{extension}"
                r = requests.get(media_url, timeout=20)
                if r.status_code == 200:
                    with open(final_filepath, 'wb') as f:
                        f.write(r.content)
                    return True, os.path.basename(final_filepath), extension
    except Exception as e:
        print(f"Error en TikWM API: {e}")
    return False, None, None


def extraer_pinterest_directo(url):
    """Extrae videos MP4 reales o imágenes HD de Pinterest."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
    }
    try:
        res = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
        
        # 1. CDN directo de Pinterest
        mp4_urls = re.findall(r'https://v1\.pinimg\.com/videos/[^\s"\'<>]+\.mp4', res.text)
        if not mp4_urls:
            mp4_urls = re.findall(r'https://[^\s"\'<>]+\.mp4', res.text)
            
        if mp4_urls:
            for video_url in mp4_urls:
                if 'pinterest' in video_url or 'pinimg' in video_url:
                    return video_url, 'mp4'

        # 2. Búsqueda en tags de OpenGraph y HTML
        soup = BeautifulSoup(res.text, 'html.parser')
        og_video = soup.find('meta', property='og:video') or soup.find('meta', property='og:video:secure_url')
        if og_video and og_video.get('content'):
            return og_video['content'], 'mp4'

        video_tag = soup.find('video')
        if video_tag and video_tag.get('src'):
            return video_tag['src'], 'mp4'

        # 3. Datos en JSON-LD
        scripts = soup.find_all('script', type='application/ld+json')
        for script in scripts:
            if script.string:
                try:
                    data = json.loads(script.string)
                    if isinstance(data, dict) and data.get('@type') == 'VideoObject' and 'contentUrl' in data:
                        return data['contentUrl'], 'mp4'
                except Exception:
                    continue

        # 4. Imagen HD como recurso final
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            img_url = og_image['content']
            img_url_hd = re.sub(r'/(x\d+|originals|\d+x)/', '/originals/', img_url)
            ext = 'png' if '.png' in img_url.lower() else ('webp' if '.webp' in img_url.lower() else 'jpg')
            return img_url_hd, ext

    except Exception:
        pass
    return None, None


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

    # Resolver redirecciones de acortadores
    link = resolver_url_final(link_bruto)
    id_unico = str(uuid.uuid4())[:8]

    # Obtener ejecutable de FFmpeg
    try:
        import imageio_ffmpeg
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_path = None

    # Parámetros optimizados anti-bloqueo
    opciones_anti_bot = [
        '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        '--extractor-args', 'youtube:player_client=android_vr,ios,mweb',
        '--no-check-certificates',
        '--no-playlist',
        '--retries', '5'
    ]

    descarga_exitosa = False
    filename = ""
    filepath_base = os.path.join(DOWNLOAD_FOLDER, f"media_{id_unico}")
    tipo_retorno = formato

    # --- CASO A: TIKTOK ---
    if 'tiktok.com' in link or 'vt.tiktok.com' in link_bruto or plataforma == 'tiktok':
        exito, archivo_generado, ext_generada = extraer_tiktok_api(link, formato, filepath_base)
        if exito:
            descarga_exitosa = True
            filename = archivo_generado
            tipo_retorno = ext_generada

    # --- CASO B: PINTEREST ---
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

    # --- CASO C: YOUTUBE O EXTRACTION CON YT-DLP ---
    if not descarga_exitosa:
        if formato == 'mp3':
            filename = f"audio_{id_unico}.mp3"
            filepath = os.path.join(DOWNLOAD_FOLDER, filename)
            comando = [
                'yt-dlp',
                '-x',
                '--audio-format', 'mp3',
                '-o', filepath
            ] + opciones_anti_bot + [link]
        else:
            filename = f"media_{id_unico}.mp4"
            filepath = os.path.join(DOWNLOAD_FOLDER, filename)
            comando = [
                'yt-dlp',
                '-f', 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best',
                '--merge-output-format', 'mp4',
                '-o', filepath
            ] + opciones_anti_bot + [link]

        if ffmpeg_path:
            comando.insert(1, '--ffmpeg-location')
            comando.insert(2, ffmpeg_path)

        try:
            subprocess.run(comando, check=True, timeout=120)
            filepath_final = os.path.join(DOWNLOAD_FOLDER, filename)
            if os.path.exists(filepath_final) and os.path.getsize(filepath_final) > 0:
                descarga_exitosa = True
                tipo_retorno = formato
        except Exception:
            descarga_exitosa = False

    # --- RESPUESTA JSON ---
    if descarga_exitosa:
        return jsonify({
            "success": True,
            "download_url": f"/obtener-archivo/{filename}",
            "filename": filename,
            "tipo": tipo_retorno
        })
    else:
        return jsonify({
            "error": "No se pudo extraer el archivo. Verifica la URL o que el contenido sea público."
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
