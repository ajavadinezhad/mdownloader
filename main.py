import asyncio
import logging
import tempfile
import shutil
import subprocess
import re
import json
import os
from urllib.parse import urlparse
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import yt_dlp
import requests
import instaloader

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DOWNLOAD_DIR = os.getenv('DOWNLOAD_DIR', './downloads')
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', '50')) * 1024 * 1024

if not BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN not found!")
    exit(1)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

class MediaDownloaderBot:
    def __init__(self):
        self.supported_platforms = {
           'youtube.com': 'YouTube',
           'youtu.be': 'YouTube',
            'twitter.com': 'Twitter/X',
            'x.com': 'Twitter/X',
           'instagram.com': 'Instagram',
            'soundcloud.com': 'SoundCloud',
        }
        self.url_cache = {}
        
        # Initialize instaloader
        self.insta_loader = instaloader.Instaloader(
            dirname_pattern='{target}',
            filename_pattern='{date_utc}_UTC',
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
        )
    
    def is_supported_url(self, url):
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if domain.startswith('www.'):
                domain = domain[4:]
            return any(platform in domain for platform in self.supported_platforms.keys())
        except:
            pass

def main():
    logger.info("🤖 Starting Media Downloader Bot...")
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(handle_format_selection))
    
    logger.info("🚀 Bot is running!")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
            return False
    
    def store_url(self, url, user_id):
        import hashlib
        import time
        url_hash = hashlib.md5(f"{url}{user_id}{time.time()}".encode()).hexdigest()[:8]
        self.url_cache[url_hash] = url
        if len(self.url_cache) > 100:
            old_keys = list(self.url_cache.keys())[:-50]
            for key in old_keys:
                del self.url_cache[key]
        return url_hash
    
    def get_url(self, url_id):
        return self.url_cache.get(url_id)
    
    def get_platform_name(self, url):
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if domain.startswith('www.'):
                domain = domain[4:]
            for platform, name in self.supported_platforms.items():
                if platform in domain:
                    return name
            return "Unknown"
        except:
            return "Unknown"
    
    async def download_youtube_improved(self, url, format_type):
        """Improved YouTube downloader with multiple fallback methods"""
        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_DIR)
        
        # Clean URL first
        clean_url = url
        if 'shorts/' in url:
            video_id_match = re.search(r'/shorts/([A-Za-z0-9_-]+)', url)
            if video_id_match:
                video_id = video_id_match.group(1)
                clean_url = f"https://www.youtube.com/watch?v={video_id}"
                logger.info(f"Converted Shorts URL: {clean_url}")
        
        # Method 1: Try yt-dlp first (most reliable)
        logger.info("Trying yt-dlp method...")
        try:
            return await self._download_with_ytdlp_youtube(clean_url, format_type, temp_dir)
        except Exception as e:
            logger.warning(f"yt-dlp method failed: {e}")
        
        # Method 2: Try yt-dlp with different extractors
        logger.info("Trying yt-dlp with different extractors...")
        try:
            return await self._download_with_ytdlp_alternative(clean_url, format_type, temp_dir)
        except Exception as e:
            logger.warning(f"yt-dlp alternative method failed: {e}")
        
        # Method 3: Try cobalt.tools API
        logger.info("Trying cobalt.tools API...")
        try:
            return await self._download_with_cobalt(clean_url, format_type, temp_dir)
        except Exception as e:
            logger.warning(f"Cobalt API method failed: {e}")
        
        # Method 4: Try y2mate API (if available)
        logger.info("Trying y2mate-like service...")
        try:
            return await self._download_with_web_service(clean_url, format_type, temp_dir)
        except Exception as e:
            logger.warning(f"Web service method failed: {e}")
        
        # All methods failed
        return None, "❌ All YouTube download methods failed. YouTube may have updated their system."
    
    async def _download_with_ytdlp_youtube(self, url, format_type, temp_dir):
        """Primary yt-dlp method with optimized settings"""
        opts = {
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'noplaylist': True,
            'retries': 5,
            'fragment_retries': 5,
            'skip_unavailable_fragments': True,
            'keepvideo': False,
            'no_warnings': False,
            'extract_flat': False,
            'writethumbnail': False,
            'writeinfojson': False,
            # Add cookies and headers to avoid bot detection
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
        }
        
        if format_type == 'audio':
            opts.update({
                'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }]
            })
        else:
            # For video, prefer formats that work well
            opts['format'] = 'best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]/best'
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            # First get info to check file size
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Unknown')
            logger.info(f"Video title: {title}")
            
            # Check file size
            filesize = info.get('filesize') or info.get('filesize_approx', 0)
            if filesize and filesize > MAX_FILE_SIZE:
                return None, f"❌ File too large ({filesize // (1024*1024)}MB). Limit: {MAX_FILE_SIZE // (1024*1024)}MB"
            
            # Download
            ydl.download([url])
            
            # Find downloaded file
            files = [f for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f))]
            if files:
                filepath = os.path.join(temp_dir, files[0])
                # Double-check file size
                actual_size = os.path.getsize(filepath)
                if actual_size > MAX_FILE_SIZE:
                    return None, f"❌ File too large ({actual_size // (1024*1024)}MB)"
                return filepath, None
            else:
                return None, "❌ Download completed but file not found"
    
    async def _download_with_ytdlp_alternative(self, url, format_type, temp_dir):
        """Alternative yt-dlp method with different settings"""
        opts = {
            'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
            'noplaylist': True,
            'retries': 3,
            'ignoreerrors': False,
            # Use different extractor options
            'extractor_args': {
                'youtube': {
                    'skip': ['dash', 'hls'],  # Skip DASH and HLS formats that might cause issues
                    'player_client': ['android', 'web'],  # Try different clients
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
            }
        }
        
        if format_type == 'audio':
            opts['format'] = 'worstaudio/worst'  # Sometimes worst quality works when best doesn't
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '128',  # Lower quality for better compatibility
            }]
        else:
            opts['format'] = 'worst[height>=360]/worst'  # Lower quality for better success rate
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            filesize = info.get('filesize') or info.get('filesize_approx', 0)
            if filesize and filesize > MAX_FILE_SIZE:
                return None, f"❌ File too large"
            
            ydl.download([url])
            
            files = [f for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f))]
            if files:
                return os.path.join(temp_dir, files[0]), None
            else:
                return None, "❌ Alternative method failed"
    
    async def _download_with_cobalt(self, url, format_type, temp_dir):
        """Try using cobalt.tools API as fallback"""
        try:
            api_url = "https://co.wuk.sh/api/json"
            
            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            data = {
                "url": url,
                "vCodec": "h264",
                "vQuality": "720",
                "aFormat": "mp3" if format_type == 'audio' else "best",
                "isAudioOnly": format_type == 'audio'
            }
            
            response = requests.post(api_url, json=data, headers=headers, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                
                if result.get('status') == 'success' or result.get('status') == 'redirect':
                    download_url = result.get('url')
                    if download_url:
                        # Download the file
                        file_response = requests.get(download_url, stream=True, timeout=60)
                        file_response.raise_for_status()
                        
                        # Determine filename
                        content_type = file_response.headers.get('content-type', '')
                        if format_type == 'audio' or 'audio' in content_type:
                            filename = "youtube_audio.mp3"
                        else:
                            filename = "youtube_video.mp4"
                        
                        filepath = os.path.join(temp_dir, filename)
                        
                        # Download with size check
                        total_size = 0
                        with open(filepath, 'wb') as f:
                            for chunk in file_response.iter_content(chunk_size=8192):
                                if chunk:
                                    total_size += len(chunk)
                                    if total_size > MAX_FILE_SIZE:
                                        return None, "❌ File too large"
                                    f.write(chunk)
                        
                        return filepath, None
                    else:
                        return None, "❌ Cobalt API: No download URL received"
                else:
                    error_msg = result.get('text', 'Unknown error')
                    return None, f"❌ Cobalt API: {error_msg}"
            else:
                return None, f"❌ Cobalt API returned status {response.status_code}"
                
        except requests.RequestException as e:
            return None, f"❌ Cobalt API network error: {str(e)[:50]}"
        except Exception as e:
            return None, f"❌ Cobalt API error: {str(e)[:50]}"
    
    async def _download_with_web_service(self, url, format_type, temp_dir):
        """Try using a web-based YouTube downloader service"""
        try:
            # Extract video ID
            video_id_match = re.search(r'(?:v=|/)([A-Za-z0-9_-]{11})', url)
            if not video_id_match:
                return None, "❌ Could not extract video ID"
            
            video_id = video_id_match.group(1)
            
            # Try a simple YouTube info API (this is a basic example)
            # Note: You might need to find working APIs or implement your own
            info_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            
            response = requests.get(info_url, timeout=10)
            if response.status_code == 200:
                info = response.json()
                title = info.get('title', 'Unknown')
                logger.info(f"Found video: {title}")
                
                # This is a placeholder - you'd need to implement actual download logic
                # using working YouTube download services or APIs
                return None, "❌ Web service method not fully implemented"
            else:
                return None, "❌ Could not get video info"
                
        except Exception as e:
            return None, f"❌ Web service error: {str(e)[:50]}"
    
    async def download_youtube(self, url, format_type):
        """Main YouTube download method - now uses improved version"""
        return await self.download_youtube_improved(url, format_type)
    
    async def download_instagram_simple(self, url, format_type):
        """Simple Instagram downloader using direct web scraping"""
        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_DIR)
        
        try:
            # Extract shortcode from URL
            shortcode_match = re.search(r'/(?:p|reel)/([A-Za-z0-9_-]+)', url)
            if not shortcode_match:
                return None, "❌ Invalid Instagram URL"
            
            shortcode = shortcode_match.group(1)
            
            # Try direct web scraping approach
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
            
            # Get the Instagram page
            page_url = f"https://www.instagram.com/p/{shortcode}/"
            response = requests.get(page_url, headers=headers, timeout=10)
            response.raise_for_status()
            
            # Extract JSON data from page
            html_content = response.text
            
            # Look for video/image URLs in the HTML
            video_pattern = r'"video_url":"([^"]+)"'
            image_pattern = r'"display_url":"([^"]+)"'
            
            video_match = re.search(video_pattern, html_content)
            image_match = re.search(image_pattern, html_content)
            
            if video_match:
                # It's a video
                video_url = video_match.group(1).replace('\\u0026', '&')
                
                # Download video
                video_response = requests.get(video_url, headers=headers, stream=True)
                video_response.raise_for_status()
                
                filename = f"instagram_video_{shortcode}.mp4"
                filepath = os.path.join(temp_dir, filename)
                
                with open(filepath, 'wb') as f:
                    for chunk in video_response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                # Check file size
                if os.path.getsize(filepath) > MAX_FILE_SIZE:
                    return None, f"❌ File too large. Limit: {MAX_FILE_SIZE // (1024*1024)}MB"
                
                # Convert to audio if requested
                if format_type == 'audio':
                    audio_filepath = os.path.join(temp_dir, f"instagram_audio_{shortcode}.mp3")
                    try:
                        cmd = ['ffmpeg', '-i', filepath, '-vn', '-acodec', 'mp3', '-ab', '192k', audio_filepath, '-y']
                        result = subprocess.run(cmd, capture_output=True, timeout=60)
                        if result.returncode == 0 and os.path.exists(audio_filepath):
                            os.remove(filepath)
                            return audio_filepath, None
                        else:
                            return None, "❌ Audio extraction failed"
                    except:
                        return None, "❌ Audio extraction failed"
                
                return filepath, None
                
            elif image_match:
                # It's an image
                if format_type == 'audio':
                    return None, "❌ This post contains only images"
                
                image_url = image_match.group(1).replace('\\u0026', '&')
                
                # Download image
                image_response = requests.get(image_url, headers=headers, stream=True)
                image_response.raise_for_status()
                
                filename = f"instagram_image_{shortcode}.jpg"
                filepath = os.path.join(temp_dir, filename)
                
                with open(filepath, 'wb') as f:
                    for chunk in image_response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                if os.path.getsize(filepath) > MAX_FILE_SIZE:
                    return None, f"❌ File too large. Limit: {MAX_FILE_SIZE // (1024*1024)}MB"
                
                return filepath, None
            else:
                return None, "❌ Could not find media in Instagram post"
                
        except requests.exceptions.RequestException as e:
            return None, "❌ Network error accessing Instagram"
        except Exception as e:
            logger.error(f"Instagram simple scraper error: {e}")
            return None, f"❌ Instagram download failed: {str(e)[:100]}"
    
    async def download_instagram(self, url, format_type):
        """Try Instaloader first, then fallback to simple scraper"""
        try:
            # First try Instaloader
            shortcode_match = re.search(r'/(?:p|reel)/([A-Za-z0-9_-]+)', url)
            if not shortcode_match:
                return None, "❌ Invalid Instagram URL"
            
            shortcode = shortcode_match.group(1)
            
            # Quick test to avoid rate limiting
            post = instaloader.Post.from_shortcode(self.insta_loader.context, shortcode)
            
            temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_DIR)
            
            if post.is_video:
                # Download video
                response = requests.get(post.video_url, stream=True)
                response.raise_for_status()
                
                filename = f"instagram_video_{shortcode}.mp4"
                filepath = os.path.join(temp_dir, filename)
                
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                # Check file size
                if os.path.getsize(filepath) > MAX_FILE_SIZE:
                    return None, f"❌ File too large. Limit: {MAX_FILE_SIZE // (1024*1024)}MB"
                
                # Convert to audio if requested
                if format_type == 'audio':
                    audio_filepath = os.path.join(temp_dir, f"instagram_audio_{shortcode}.mp3")
                    try:
                        cmd = ['ffmpeg', '-i', filepath, '-vn', '-acodec', 'mp3', '-ab', '192k', audio_filepath, '-y']
                        result = subprocess.run(cmd, capture_output=True, timeout=60)
                        if result.returncode == 0 and os.path.exists(audio_filepath):
                            os.remove(filepath)
                            return audio_filepath, None
                        else:
                            return None, "❌ Audio extraction failed"
                    except:
                        return None, "❌ Audio extraction failed"
                
                return filepath, None
            else:
                # Download image
                if format_type == 'audio':
                    return None, "❌ This post contains only images"
                
                response = requests.get(post.url, stream=True)
                response.raise_for_status()
                
                filename = f"instagram_image_{shortcode}.jpg"
                filepath = os.path.join(temp_dir, filename)
                
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                if os.path.getsize(filepath) > MAX_FILE_SIZE:
                    return None, f"❌ File too large. Limit: {MAX_FILE_SIZE // (1024*1024)}MB"
                
                return filepath, None
                
        except Exception as e:
            logger.warning(f"Instaloader failed: {e}")
            
            # If Instaloader fails (rate limit, etc), try simple scraper
            if '401' in str(e) or 'rate limit' in str(e).lower() or 'unauthorized' in str(e).lower():
                logger.info("Trying Instagram simple scraper fallback...")
                return await self.download_instagram_simple(url, format_type)
            else:
                return None, f"❌ Instagram download failed: {str(e)[:100]}"
    
    async def download_with_ytdlp(self, url, format_type, platform_name):
        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_DIR)
        
        try:
            opts = {
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'noplaylist': True,
                'retries': 3,
            }
            
            if format_type == 'audio':
                opts.update({
                    'format': 'bestaudio/best',
                    'extractaudio': True,
                    'audioformat': 'mp3',
                    'audioquality': '192',
                })
            else:
                opts['format'] = 'best[height<=720]/best'
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                # Convert x.com to twitter.com
                if 'x.com' in url:
                    url = url.replace('x.com', 'twitter.com')
                
                info = ydl.extract_info(url, download=False)
                filesize = info.get('filesize') or info.get('filesize_approx', 0)
                if filesize and filesize > MAX_FILE_SIZE:
                    return None, f"❌ File too large ({filesize // (1024*1024)}MB)"
                
                ydl.download([url])
                
                files = os.listdir(temp_dir)
                if files:
                    return os.path.join(temp_dir, files[0]), None
                else:
                    return None, "❌ Download failed"
                    
        except Exception as e:
            logger.error(f"yt-dlp error: {e}")
            if 'private' in str(e).lower():
                return None, "❌ Content is private"
            elif 'unavailable' in str(e).lower():
                return None, "❌ Content unavailable"
            else:
                return None, f"❌ Download failed: {str(e)[:100]}"
    
    async def download_media(self, url, format_type='best'):
        if any(platform in url.lower() for platform in ['youtube.com', 'youtu.be']):
            return await self.download_youtube(url, format_type)
        elif 'instagram.com' in url.lower():
            return await self.download_instagram(url, format_type)
        else:
            return await self.download_with_ytdlp(url, format_type, self.get_platform_name(url))

# Initialize bot
bot = MediaDownloaderBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = f"""
🎬 <b>Media Downloader Bot</b>

📱 <b>Supported:</b>
• YouTube (videos & audio)
• Twitter/X (videos)
• Instagram (posts & reels)
• SoundCloud (audio)

📝 <b>Usage:</b>
Send me a URL and choose format.

⚠️ <b>Limit:</b> {MAX_FILE_SIZE // (1024*1024)}MB max file size.
    """
    await update.message.reply_text(welcome_text, parse_mode='HTML')

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    # Extract URL
    url_pattern = r'https?://[^\s]+'
    urls = re.findall(url_pattern, text)
    if not urls:
        return
    
    url = urls[0]
    
    if not bot.is_supported_url(url):
        return
    
    platform = bot.get_platform_name(url)
    url_id = bot.store_url(url, update.message.from_user.id)
    
    # Create buttons based on platform
    if 'soundcloud.com' in url.lower():
        keyboard = [[InlineKeyboardButton("🎵 Audio", callback_data=f"audio|{url_id}")]]
    else:
        keyboard = [
            [
                InlineKeyboardButton("🎥 Video", callback_data=f"video|{url_id}"),
                InlineKeyboardButton("🎵 Audio", callback_data=f"audio|{url_id}")
            ]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"📱 {platform} detected. Choose format:", reply_markup=reply_markup)

async def handle_format_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        format_type, url_id = query.data.split('|', 1)
        url = bot.get_url(url_id)
        
        if not url:
            await query.edit_message_text("❌ Session expired. Send URL again.")
            return
        
        platform = bot.get_platform_name(url)
        format_emoji = "🎥" if format_type == "video" else "🎵"
        
        await query.edit_message_text(f"{format_emoji} Downloading {format_type} from {platform}...")
        
        # Download
        filepath, error = await bot.download_media(url, format_type)
        
        if error:
            await query.edit_message_text(error)
            return
        
        if not filepath or not os.path.exists(filepath):
            await query.edit_message_text("❌ Download failed")
            return
        
        # Check file size
        file_size = os.path.getsize(filepath)
        if file_size > MAX_FILE_SIZE:
            await query.edit_message_text(f"❌ File too large ({file_size // (1024*1024)}MB)")
            return
        
        await query.edit_message_text("📤 Uploading...")
        
        # Send file
        caption = f"Downloaded from {platform}"
        with open(filepath, 'rb') as file:
            if format_type == 'audio':
                await context.bot.send_audio(query.message.chat_id, audio=file, caption=caption)
            elif filepath.lower().endswith(('.jpg', '.jpeg', '.png')):
                await context.bot.send_photo(query.message.chat_id, photo=file, caption=caption)
            else:
                await context.bot.send_video(query.message.chat_id, video=file, caption=caption)
        
        await query.edit_message_text("✅ Download complete!")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)[:100]}")
    
    finally:
        # Cleanup
        try:
            if 'filepath' in locals() and filepath and os.path.exists(filepath):
                temp_dir = os.path.dirname(filepath)
                os.remove(filepath)
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            logger.error("Failed to clean up temporary files")