import os
import asyncio
import logging
import tempfile
import shutil
import subprocess
import json
import re
from urllib.parse import urlparse
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import yt_dlp
import requests
import instaloader

# New YouTube library
from pytubefix import YouTube
from pytubefix.exceptions import VideoUnavailable, AgeRestrictedError, VideoPrivate, MembersOnly
import io

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Bot configuration from environment variables
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DOWNLOAD_DIR = os.getenv('DOWNLOAD_DIR', './downloads')
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', '50')) * 1024 * 1024  # Convert MB to bytes

# Validate bot token
if not BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN not found in environment variables!")
    logger.error("💡 Create a .env file with your bot token:")
    logger.error("   TELEGRAM_BOT_TOKEN=your_bot_token_here")
    exit(1)

# Ensure download directory exists
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
        # Store URLs temporarily with short IDs
        self.url_cache = {}
        # Initialize instaloader
        self.insta_loader = instaloader.Instaloader(
            dirname_pattern='{target}',
            filename_pattern='{date_utc}_UTC',
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            sleep=True,
            max_connection_attempts=3,
        )
        # Configure session settings for better reliability
        self.insta_loader.context._session.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
    
    def is_supported_url(self, url):
        """Check if the URL is from a supported platform"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Remove 'www.' prefix if present
            if domain.startswith('www.'):
                domain = domain[4:]
            
            is_supported = any(platform in domain for platform in self.supported_platforms.keys())
            return is_supported
        except Exception as e:
            logger.error(f"Error parsing URL {url}: {e}")
            return False
    
    def store_url(self, url, user_id):
        """Store URL with a short ID for callback data"""
        import hashlib
        import time
        
        # Create a short unique ID
        url_hash = hashlib.md5(f"{url}{user_id}{time.time()}".encode()).hexdigest()[:8]
        self.url_cache[url_hash] = url
        
        # Clean old entries (keep only last 100)
        if len(self.url_cache) > 100:
            old_keys = list(self.url_cache.keys())[:-50]
            for key in old_keys:
                del self.url_cache[key]
        
        return url_hash
    
    def get_url(self, url_id):
        """Retrieve URL from short ID"""
        return self.url_cache.get(url_id)
    
    def get_platform_name(self, url):
        """Get the platform name from URL"""
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
    
    async def download_youtube_with_pytubefix(self, url, format_type):
        """Download YouTube content using pytubefix"""
        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_DIR)
        
        try:
            logger.info(f"🎬 YouTube download with PyTubeFix: {url}")
            
            # Clean URL for better compatibility
            clean_url = url
            if 'shorts/' in url:
                # Convert YouTube Shorts URL to regular format
                import re
                video_id_match = re.search(r'/shorts/([A-Za-z0-9_-]+)', url)
                if video_id_match:
                    video_id = video_id_match.group(1)
                    clean_url = f"https://www.youtube.com/watch?v={video_id}"
                    logger.info(f"🔄 Converted Shorts URL to: {clean_url}")
            
            # Create YouTube object with better error handling
            try:
                yt = YouTube(clean_url, use_oauth=False, allow_oauth_cache=False)
            except Exception as e:
                logger.warning(f"First attempt failed: {e}")
                # Try with original URL
                yt = YouTube(url, use_oauth=False, allow_oauth_cache=False)
            
            # Get video info
            title = yt.title
            duration = yt.length
            
            logger.info(f"📺 Title: {title}")
            logger.info(f"⏱️ Duration: {duration}s")
            
            if format_type == 'audio':
                # Get audio stream
                try:
                    # Try to get best audio quality
                    audio_stream = yt.streams.filter(only_audio=True, file_extension='mp4').order_by('abr').desc().first()
                    
                    if not audio_stream:
                        # Fallback to any audio stream
                        audio_stream = yt.streams.filter(only_audio=True).first()
                    
                    if not audio_stream:
                        return None, "❌ No audio stream available"
                    
                    logger.info(f"🎵 Audio quality: {audio_stream.abr}")
                    
                    # Check estimated file size
                    if audio_stream.filesize and audio_stream.filesize > MAX_FILE_SIZE:
                        size_mb = audio_stream.filesize // (1024 * 1024)
                        max_mb = MAX_FILE_SIZE // (1024 * 1024)
                        return None, f"❌ Audio too large ({size_mb}MB). Limit: {max_mb}MB"
                    
                    # Download audio
                    safe_title = re.sub(r'[^\w\s-]', '', title)[:50]
                    filename = f"{safe_title}_audio.mp4"
                    filepath = os.path.join(temp_dir, filename)
                    
                    audio_stream.download(output_path=temp_dir, filename=filename)
                    
                    # Convert to MP3 if ffmpeg available
                    mp3_filepath = os.path.join(temp_dir, f"{safe_title}_audio.mp3")
                    try:
                        cmd = ['ffmpeg', '-i', filepath, '-vn', '-acodec', 'mp3', '-ab', '192k', mp3_filepath, '-y']
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                        
                        if result.returncode == 0 and os.path.exists(mp3_filepath):
                            os.remove(filepath)  # Remove original mp4
                            filepath = mp3_filepath
                            logger.info("✅ Converted to MP3")
                        else:
                            logger.info("ℹ️ Using MP4 audio (ffmpeg conversion failed)")
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        logger.info("ℹ️ Using MP4 audio (ffmpeg not available)")
                    
                    return filepath, None
                    
                except Exception as e:
                    logger.error(f"Audio download error: {e}")
                    return None, f"❌ Audio download failed: {str(e)[:100]}"
            
            else:
                # Get video stream
                try:
                    # Try different quality options
                    video_stream = None
                    
                    # First try 720p with audio
                    video_stream = yt.streams.filter(progressive=True, file_extension='mp4', res='720p').first()
                    
                    if not video_stream:
                        # Try 480p with audio
                        video_stream = yt.streams.filter(progressive=True, file_extension='mp4', res='480p').first()
                    
                    if not video_stream:
                        # Try 360p with audio
                        video_stream = yt.streams.filter(progressive=True, file_extension='mp4', res='360p').first()
                    
                    if not video_stream:
                        # Fallback to highest quality progressive
                        video_stream = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first()
                    
                    if not video_stream:
                        # Last resort - any progressive stream
                        video_stream = yt.streams.filter(progressive=True).first()
                    
                    if not video_stream:
                        return None, "❌ No suitable video stream found"
                    
                    logger.info(f"🎥 Video quality: {video_stream.resolution}")
                    
                    # Check file size
                    if video_stream.filesize and video_stream.filesize > MAX_FILE_SIZE:
                        size_mb = video_stream.filesize // (1024 * 1024)
                        max_mb = MAX_FILE_SIZE // (1024 * 1024)
                        return None, f"❌ Video too large ({size_mb}MB). Limit: {max_mb}MB"
                    
                    # Download video
                    safe_title = re.sub(r'[^\w\s-]', '', title)[:50]
                    filename = f"{safe_title}_video.mp4"
                    filepath = os.path.join(temp_dir, filename)
                    
                    video_stream.download(output_path=temp_dir, filename=filename)
                    
                    return filepath, None
                    
                except Exception as e:
                    logger.error(f"Video download error: {e}")
                    return None, f"❌ Video download failed: {str(e)[:100]}"
        
        except VideoUnavailable:
            return None, "❌ YouTube video is unavailable, private, or deleted"
        except AgeRestrictedError:
            return None, "❌ Age-restricted content cannot be downloaded"
        except VideoPrivate:
            return None, "❌ This YouTube video is private"
        except MembersOnly:
            return None, "❌ This is members-only content"
        except Exception as e:
            error_msg = str(e).lower()
            logger.error(f"PyTubeFix error: {e}")
            
            if 'regex' in error_msg or 'extract' in error_msg or 'cipher' in error_msg:
                return None, "❌ YouTube changed their system. Trying fallback method..."
            elif 'unavailable' in error_msg or 'not found' in error_msg:
                return None, "❌ Video unavailable. May be deleted, private, or region-blocked"
            elif 'private' in error_msg:
                return None, "❌ This video is private"
            elif 'sign in' in error_msg or 'login' in error_msg:
                return None, "❌ This video requires login to view"
            elif 'restricted' in error_msg:
                return None, "❌ Video is restricted in your region"
            else:
                return None, f"❌ YouTube error: {str(e)[:100]}"
    
    async def download_instagram_with_instaloader(self, url, format_type):
        """Download Instagram content using instaloader"""
        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_DIR)
        
        try:
            # Configure instaloader for this download
            self.insta_loader.dirname_pattern = temp_dir
            
            # Extract shortcode from URL
            import re
            shortcode_match = re.search(r'/p/([A-Za-z0-9_-]+)', url) or re.search(r'/reel/([A-Za-z0-9_-]+)', url)
            if not shortcode_match:
                return None, "❌ Invalid Instagram URL format"
            
            shortcode = shortcode_match.group(1)
            logger.info(f"📱 Instagram shortcode: {shortcode}")
            
            # Download the post
            try:
                post = instaloader.Post.from_shortcode(self.insta_loader.context, shortcode)
                
                # Check if it's a video or image
                if post.is_video:
                    # Download video
                    video_url = post.video_url
                    response = requests.get(video_url, stream=True)
                    response.raise_for_status()
                    
                    # Determine file extension
                    content_type = response.headers.get('content-type', '')
                    if 'mp4' in content_type:
                        ext = '.mp4'
                    else:
                        ext = '.mp4'  # Default to mp4
                    
                    filename = f"instagram_video_{shortcode}{ext}"
                    filepath = os.path.join(temp_dir, filename)
                    
                    # Download video file
                    with open(filepath, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    
                    # Check file size
                    file_size = os.path.getsize(filepath)
                    max_size_mb = MAX_FILE_SIZE // (1024 * 1024)
                    if file_size > MAX_FILE_SIZE:
                        return None, f"❌ File too large ({file_size // (1024*1024)}MB). Telegram limit is {max_size_mb}MB."
                    
                    # If user wants audio, convert it
                    if format_type == 'audio':
                        audio_filepath = os.path.join(temp_dir, f"instagram_audio_{shortcode}.mp3")
                        try:
                            # Use ffmpeg to extract audio
                            cmd = ['ffmpeg', '-i', filepath, '-vn', '-acodec', 'mp3', '-ab', '192k', audio_filepath, '-y']
                            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                            
                            if result.returncode == 0 and os.path.exists(audio_filepath):
                                os.remove(filepath)  # Remove video file
                                return audio_filepath, None
                            else:
                                return None, "❌ Audio extraction failed. ffmpeg may not be installed."
                        except (FileNotFoundError, subprocess.TimeoutExpired):
                            return None, "❌ Audio extraction failed. ffmpeg required for audio conversion."
                    
                    return filepath, None
                    
                else:
                    # It's an image
                    if format_type == 'audio':
                        return None, "❌ This Instagram post contains only images. Audio extraction not possible."
                    
                    # Download image
                    image_url = post.url
                    response = requests.get(image_url, stream=True)
                    response.raise_for_status()
                    
                    # Determine file extension
                    content_type = response.headers.get('content-type', '')
                    if 'jpeg' in content_type or 'jpg' in content_type:
                        ext = '.jpg'
                    elif 'png' in content_type:
                        ext = '.png'
                    else:
                        ext = '.jpg'  # Default
                    
                    filename = f"instagram_image_{shortcode}{ext}"
                    filepath = os.path.join(temp_dir, filename)
                    
                    with open(filepath, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    
                    # Check file size
                    file_size = os.path.getsize(filepath)
                    max_size_mb = MAX_FILE_SIZE // (1024 * 1024)
                    if file_size > MAX_FILE_SIZE:
                        return None, f"❌ File too large ({file_size // (1024*1024)}MB). Telegram limit is {max_size_mb}MB."
                    
                    return filepath, None
                    
            except instaloader.exceptions.PostChangedException:
                return None, "❌ Instagram post was modified or deleted"
            except instaloader.exceptions.PrivateProfileNotFollowedException:
                return None, "❌ This is a private Instagram account"
            except instaloader.exceptions.ProfileNotExistsException:
                return None, "❌ Instagram profile does not exist"
            except instaloader.exceptions.LoginRequiredException:
                return None, "❌ Instagram login required for this content"
            except requests.exceptions.RequestException as e:
                return None, f"❌ Network error: {str(e)[:50]}..."
            except Exception as e:
                logger.error(f"Instaloader error: {e}")
                return None, f"❌ Instagram download failed: {str(e)[:100]}..."
                
        except Exception as e:
            logger.error(f"Instagram download error: {e}")
            return None, f"❌ Instagram error: {str(e)[:100]}..."
    
    async def download_with_ytdlp(self, url, format_type, platform_name):
        """Download using yt-dlp for other platforms"""
        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_DIR)
        
        try:
            # Base yt-dlp options for other platforms
            base_opts = {
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'format': format_type,
                'noplaylist': True,
                'extractaudio': format_type == 'audio',
                'audioformat': 'mp3' if format_type == 'audio' else None,
                'audioquality': '192' if format_type == 'audio' else None,
                'retries': 3,
                'fragment_retries': 3,
                'skip_unavailable_fragments': True,
                'abort_on_unavailable_fragment': False,
                'writesubtitles': False,
                'writeautomaticsub': False,
                'ignoreerrors': False,
            }
            
            strategies = []
            
            if 'twitter.com' in url or 'x.com' in url:
                # Twitter Strategy
                strategy1 = base_opts.copy()
                strategy1.update({
                    'format': 'best[height<=720]/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    },
                })
                strategies.append(strategy1)
                
            elif 'soundcloud.com' in url:
                # SoundCloud strategy
                strategy = base_opts.copy()
                strategy.update({
                    'format': 'bestaudio/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    }
                })
                if format_type == 'audio':
                    strategy['postprocessors'] = [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }]
                strategies.append(strategy)
                
            else:
                # Default strategy for other platforms
                strategy = base_opts.copy()
                strategy.update({
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    }
                })
                strategies.append(strategy)
            
            # Try each strategy
            last_error = None
            for i, strategy in enumerate(strategies):
                try:
                    logger.info(f"Trying {platform_name} strategy {i+1}/{len(strategies)}")
                    
                    # Handle URL conversion for Twitter
                    current_url = url
                    if 'x.com' in url and 'twitter.com' not in url:
                        current_url = url.replace('x.com', 'twitter.com')
                    
                    with yt_dlp.YoutubeDL(strategy) as ydl:
                        # Extract info first
                        try:
                            info = ydl.extract_info(current_url, download=False)
                        except Exception as e:
                            error_msg = str(e).lower()
                            logger.warning(f"Strategy {i+1} failed during info extraction: {e}")
                            
                            if i < len(strategies) - 1:
                                continue
                            return None, f"❌ Error accessing content: {str(e)[:100]}..."
                        
                        title = info.get('title', 'Unknown')
                        
                        # Check file size
                        filesize = info.get('filesize') or info.get('filesize_approx', 0)
                        max_size_mb = MAX_FILE_SIZE // (1024 * 1024)
                        if filesize and filesize > MAX_FILE_SIZE:
                            return None, f"❌ File too large ({filesize // (1024*1024)}MB). Telegram limit is {max_size_mb}MB."
                        
                        # Download the media
                        try:
                            ydl.download([current_url])
                            
                            # Find downloaded file
                            files = os.listdir(temp_dir)
                            if files:
                                filepath = os.path.join(temp_dir, files[0])
                                logger.info(f"✅ {platform_name} download success with strategy {i+1}")
                                return filepath, None
                            else:
                                if i < len(strategies) - 1:
                                    continue
                                return None, "❌ Download failed - no file created"
                        
                        except Exception as e:
                            last_error = str(e)
                            logger.warning(f"Strategy {i+1} failed during download: {e}")
                            
                            if i < len(strategies) - 1:
                                continue
                            return None, f"❌ Download failed: {str(e)[:100]}..."
                
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"Strategy {i+1} failed with exception: {e}")
                    if i < len(strategies) - 1:
                        continue
                    break
            
            # All strategies failed
            return None, f"❌ All {platform_name} strategies failed. Last error: {str(last_error)[:100] if last_error else 'Unknown'}..."
        
        except Exception as e:
            logger.error(f"Unexpected error in download_with_ytdlp: {e}")
            return None, f"❌ Unexpected error: {str(e)[:100]}..."
    
    async def download_media(self, url, format_type='best'):
        """Download media - route to appropriate downloader"""
        platform_name = self.get_platform_name(url)
        
        # Use PyTubeFix for YouTube with fallback to yt-dlp
        if any(platform in url.lower() for platform in ['youtube.com', 'youtu.be']):
            logger.info(f"🎬 {platform_name} detected - using PyTubeFix")
            filepath, error = await self.download_youtube_with_pytubefix(url, format_type)
            
            # If PyTubeFix fails, try yt-dlp as fallback
            if error and ("changed their system" in error or "cipher" in error or "extract" in error):
                logger.warning(f"PyTubeFix failed: {error}")
                logger.info(f"🔄 Trying yt-dlp fallback for {platform_name}")
                return await self.download_youtube_with_ytdlp_fallback(url, format_type)
            
            return filepath, error
        
        # Use instaloader for Instagram
        elif 'instagram.com' in url.lower():
            logger.info(f"📱 {platform_name} detected - using instaloader")
            return await self.download_instagram_with_instaloader(url, format_type)
        
        # Use yt-dlp for other platforms
        else:
            logger.info(f"🌐 {platform_name} detected - using yt-dlp")
            return await self.download_with_ytdlp(url, format_type, platform_name)
    
    async def download_youtube_with_ytdlp_fallback(self, url, format_type):
        """Fallback YouTube downloader using yt-dlp"""
        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_DIR)
        
        try:
            logger.info(f"🔄 YouTube fallback with yt-dlp: {url}")
            
            # Base options for YouTube
            base_opts = {
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'noplaylist': True,
                'retries': 3,
                'fragment_retries': 3,
                'writesubtitles': False,
                'writeautomaticsub': False,
                'ignoreerrors': False,
            }
            
            if format_type == 'audio':
                base_opts.update({
                    'format': 'bestaudio/best',
                    'extractaudio': True,
                    'audioformat': 'mp3',
                    'audioquality': '192',
                })
            else:
                base_opts.update({
                    'format': 'best[height<=720]/best[height<=480]/best',
                })
            
            # Add user agent
            base_opts['http_headers'] = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            with yt_dlp.YoutubeDL(base_opts) as ydl:
                try:
                    # Extract info first
                    info = ydl.extract_info(url, download=False)
                    title = info.get('title', 'Unknown')
                    
                    # Check file size
                    filesize = info.get('filesize') or info.get('filesize_approx', 0)
                    max_size_mb = MAX_FILE_SIZE // (1024 * 1024)
                    if filesize and filesize > MAX_FILE_SIZE:
                        return None, f"❌ File too large ({filesize // (1024*1024)}MB). Telegram limit is {max_size_mb}MB."
                    
                    # Download
                    ydl.download([url])
                    
                    # Find downloaded file
                    files = os.listdir(temp_dir)
                    if files:
                        filepath = os.path.join(temp_dir, files[0])
                        logger.info(f"✅ YouTube fallback download success")
                        return filepath, None
                    else:
                        return None, "❌ Fallback download failed - no file created"
                        
                except Exception as e:
                    logger.error(f"yt-dlp fallback error: {e}")
                    return None, f"❌ Both PyTubeFix and yt-dlp failed. Video may be unavailable."
                    
        except Exception as e:
            logger.error(f"Fallback method error: {e}")
            return None, f"❌ Fallback download failed: {str(e)[:100]}"

# Initialize bot
bot = MediaDownloaderBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    max_size_mb = MAX_FILE_SIZE // (1024*1024)
    welcome_text = f"""
🎬 <b>Enhanced Media Downloader Bot</b>

Send me a URL from any of these platforms and I'll download it for you:

📱 <b>Supported Platforms:</b>
• YouTube (videos & audio) 🎬 - <i>PyTubeFix powered</i>
• Twitter/X (videos) 🐦
• Instagram (posts & reels) 📸 - <i>Instaloader powered</i>
• SoundCloud (audio) 🎧

📝 <b>How to use:</b>
1. Send me a URL
2. Choose video or audio format
3. I'll download and send it to you!

⚠️ <b>Note:</b> Files must be under {max_size_mb}MB due to Telegram limits.

🚀 <b>Enhanced Features:</b>
• YouTube: Fast PyTubeFix library (no external dependencies)
• Instagram: Reliable instaloader integration
• Better error handling and user feedback
    """
    
    await update.message.reply_text(welcome_text, parse_mode='HTML')

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle URL messages"""
    original_text = update.message.text.strip()
    
    # Remove bot mention if present (for group chats)
    text = original_text
    if update.message.chat.type in ['group', 'supergroup']:
        text = re.sub(r'@\w+', '', text).strip()
    
    # Extract URL from text
    url_pattern = r'https?://[^\s]+'
    urls = re.findall(url_pattern, text)
    
    url = None
    if urls:
        url = urls[0]  # Take the first URL found
    else:
        # If no URL found in cleaned text, try original text
        urls = re.findall(url_pattern, original_text)
        if urls:
            url = urls[0]
    
    # If no URL found, return silently
    if not url:
        logger.info("No URL found in message")
        return
    
    logger.info(f"Received URL: {url}")
    
    # Check if platform is supported
    if not bot.is_supported_url(url):
        return
    
    platform = bot.get_platform_name(url)
    logger.info(f"Detected platform: {platform}")
    
    # Store URL with short ID for callback data
    url_id = bot.store_url(url, update.message.from_user.id)
    
    # Platform-specific format options
    if 'soundcloud.com' in url.lower():
        # SoundCloud - only audio
        keyboard = [
            [InlineKeyboardButton("🎵 Download Audio", callback_data=f"audio|{url_id}")]
        ]
        format_text = "Choose download format:"
    elif any(platform in url.lower() for platform in ['youtube.com', 'youtu.be']):
        # YouTube - PyTubeFix powered with yt-dlp fallback
        keyboard = [
            [
                InlineKeyboardButton("🎥 Video (Smart)", callback_data=f"video|{url_id}"),
                InlineKeyboardButton("🎵 Audio (Smart)", callback_data=f"audio|{url_id}")
            ]
        ]
        format_text = "Choose download format (PyTubeFix + yt-dlp fallback):"
    elif 'instagram.com' in url.lower():
        # Instagram - using instaloader
        keyboard = [
            [
                InlineKeyboardButton("🎥 Video/Image", callback_data=f"video|{url_id}"),
                InlineKeyboardButton("🎵 Audio (Video only)", callback_data=f"audio|{url_id}")
            ]
        ]
        format_text = "Choose download format (Instaloader powered):"
    else:
        # Other platforms - standard options
        keyboard = [
            [
                InlineKeyboardButton("🎥 Video", callback_data=f"video|{url_id}"),
                InlineKeyboardButton("🎵 Audio", callback_data=f"audio|{url_id}")
            ]
        ]
        format_text = "Choose download format:"
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📱 Detected: {platform}\n\n{format_text}",
        reply_markup=reply_markup
    )

async def handle_format_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle format selection callback"""
    query = update.callback_query
    await query.answer()
    
    data_parts = query.data.split('|', 1)
    if len(data_parts) != 2:
        await query.edit_message_text("❌ Invalid selection")
        return
    
    format_type, url_id = data_parts
    
    # Retrieve the actual URL
    url = bot.get_url(url_id)
    if not url:
        await query.edit_message_text("❌ Session expired. Please send the URL again.")
        return
    
    platform = bot.get_platform_name(url)
    
    # Show downloading message with platform-specific info
    format_emoji = "🎥" if format_type == "video" else "🎵"
    
    if platform == 'Instagram':
        download_msg = f"{format_emoji} Downloading {format_type} from {platform} (Instaloader)...\n\n⏳ Using specialized Instagram downloader for better reliability."
    elif platform == 'YouTube':
        download_msg = f"{format_emoji} Downloading {format_type} from {platform} (Smart Method)...\n\n⏳ Using PyTubeFix with yt-dlp fallback for maximum reliability!"
    else:
        download_msg = f"{format_emoji} Downloading {format_type} from {platform}...\n\n⏳ This may take a moment depending on file size."
    
    await query.edit_message_text(download_msg)
    
    # Download the media
    try:
        filepath, error = await bot.download_media(url, format_type)
        
        if error:
            error_text = f"❌ {error}"
            
            # Add helpful suggestions for common errors
            if "detected automation" in error.lower() or "bot detection" in error.lower():
                error_text += "\n\n💡 Suggestions:\n"
                error_text += "• Try different content from the same platform\n"
                error_text += "• Wait 10-15 minutes before trying again\n"
                error_text += "• Public content works better than private/restricted"
            elif "private" in error.lower() or "unavailable" in error.lower():
                if platform == 'Instagram':
                    error_text += "\n\n💡 Instagram Tips:\n"
                    error_text += "• Make sure the account is public\n"
                    error_text += "• Check if the post still exists\n"
                    error_text += "• Some accounts may require login"
                elif platform == 'YouTube':
                    error_text += "\n\n💡 YouTube Tips:\n"
                    error_text += "• Check if the video is public and available\n"
                    error_text += "• Video might be region-blocked\n"
                    error_text += "• Try a different YouTube video"
                else:
                    error_text += "\n\n💡 Try: Make sure the content is public and available in your region"
            elif "age-restricted" in error.lower():
                error_text += "\n\n💡 Note: Age-restricted content cannot be downloaded"
            elif "timed out" in error.lower():
                error_text += "\n\n💡 Try: Content might be too large. Try shorter videos or audio format"
            elif "regex" in error.lower() or "extract" in error.lower():
                if platform == 'YouTube':
                    error_text += "\n\n💡 YouTube may have updated their system. PyTubeFix will be updated soon!"
                else:
                    error_text += "\n\n💡 Platform may have changed. Try again later or different content"
            
            await query.edit_message_text(error_text)
            return
        
        if not filepath or not os.path.exists(filepath):
            await query.edit_message_text("❌ Download failed - file not found")
            return
        
        # Get file info
        file_size = os.path.getsize(filepath)
        max_size_mb = MAX_FILE_SIZE // (1024 * 1024)
        if file_size > MAX_FILE_SIZE:
            await query.edit_message_text(f"❌ File too large ({file_size // (1024*1024)}MB). Telegram limit is {max_size_mb}MB.")
            return
        
        # Send the file
        success_msg = f"📤 Uploading {format_type}..."
        if platform == 'Instagram':
            success_msg += " (Instaloader succeeded!)"
        elif platform == 'YouTube':
            success_msg += " (PyTubeFix succeeded!)"
        
        await query.edit_message_text(success_msg)
        
        # Prepare caption
        if platform == 'Instagram':
            caption = f"🎵 Downloaded from {platform} (Instaloader)" if format_type == 'audio' else f"🎥 Downloaded from {platform} (Instaloader)"
        elif platform == 'YouTube':
            caption = f"🎵 Downloaded from {platform} (PyTubeFix)" if format_type == 'audio' else f"🎥 Downloaded from {platform} (PyTubeFix)"
        else:
            caption = f"🎵 Downloaded from {platform}" if format_type == 'audio' else f"🎥 Downloaded from {platform}"
        
        with open(filepath, 'rb') as file:
            if format_type == 'audio':
                await context.bot.send_audio(
                    chat_id=query.message.chat_id,
                    audio=file,
                    caption=caption
                )
            else:
                # For Instagram images, send as photo; for videos, send as video
                if platform == 'Instagram' and filepath.lower().endswith(('.jpg', '.jpeg', '.png')):
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=file,
                        caption=caption
                    )
                else:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=file,
                        caption=caption,
                        supports_streaming=True
                    )
        
        final_msg = f"✅ {format_type.title()} downloaded successfully!"
        if platform == 'Instagram':
            final_msg += f"\n📸 Instaloader method used for {platform}"
        elif platform == 'YouTube':
            final_msg += f"\n🚀 PyTubeFix method used for {platform}"
        
        await query.edit_message_text(final_msg)
        
    except Exception as e:
        logger.error(f"Error in download process: {e}")
        await query.edit_message_text(f"❌ An error occurred: {str(e)}")

    finally:
        # Clean up temporary files
        try:
            if 'filepath' in locals() and filepath and os.path.exists(filepath):
                # Get temp directory from filepath
                temp_dir = os.path.dirname(filepath)

                os.remove(filepath)
                logger.info(f"🗑️ Deleted file: {os.path.basename(filepath)}")

                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.info(f"🗑️ Deleted temp directory: {os.path.basename(temp_dir)}")

        except Exception as e:
            logger.warning(f"⚠️ Cleanup failed: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    max_size_mb = MAX_FILE_SIZE // (1024*1024)
    help_text = f"""
🆘 Help - Enhanced Media Downloader Bot

📝 How to download:
1. Copy a URL from supported platforms
2. Send it to this bot
3. Choose video or audio format
4. Wait for download and upload

📱 Supported Platforms:
• YouTube - Videos and Audio 🎬 (PyTubeFix)
• Twitter/X - Videos and GIFs 🐦
• Instagram - Posts, Reels, Images 📸 (Instaloader)
• SoundCloud - Audio tracks 🎧

🚀 Enhanced Features:
• PyTubeFix for YouTube: Fast, reliable, no external dependencies
• Instaloader for Instagram: Specialized downloader
• Better error handling with helpful suggestions
• Automatic format optimization

⚠️ Limitations:
• Maximum file size: {max_size_mb}MB
• Audio is converted to MP3 format (when ffmpeg available)
• Some private or age-restricted content may not work

💡 Tips:
• Public content works better than private
• If YouTube download fails, try different content
• Instagram works best with public accounts
• SoundCloud tracks must be publicly available

🔧 Technical Info:
• YouTube: Uses PyTubeFix library (pure Python)
• Instagram: Uses Instaloader library
• Other platforms: Uses yt-dlp library
• Audio conversion: ffmpeg (optional)
    """
    
    await update.message.reply_text(help_text)

def main():
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("❌ Bot token not configured!")
        return
    
    logger.info("🤖 Initializing Enhanced Media Downloader Bot with PyTubeFix...")
    logger.info(f"📁 Download directory: {DOWNLOAD_DIR}")
    logger.info(f"📏 Max file size: {MAX_FILE_SIZE // (1024*1024)}MB")
    
    # Check PyTubeFix availability
    try:
        from pytubefix import YouTube
        logger.info("✅ PyTubeFix library available for YouTube downloads")
    except ImportError:
        logger.error("❌ PyTubeFix not found! Install with: pip install pytubefix")
        return
    
    # Check Instaloader availability
    try:
        import instaloader
        logger.info("✅ Instaloader library available for Instagram downloads")
    except ImportError:
        logger.warning("⚠️ Instaloader not found! Instagram downloads will fail. Install with: pip install instaloader")
    
    # Check yt-dlp availability
    try:
        import yt_dlp
        logger.info("✅ yt-dlp library available for other platforms")
    except ImportError:
        logger.warning("⚠️ yt-dlp not found! Other platform downloads will fail. Install with: pip install yt-dlp")
    
    # Check ffmpeg for audio conversion
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            logger.info("✅ ffmpeg available for audio conversion")
        else:
            logger.warning("⚠️ ffmpeg command failed")
    except FileNotFoundError:
        logger.warning("⚠️ ffmpeg not found! Audio conversion will be limited")
    except Exception as e:
        logger.warning(f"⚠️ Could not check ffmpeg: {e}")
    
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(handle_format_selection))
    
    # Run the bot until the user presses Ctrl-C
    logger.info("🚀 Enhanced Bot is running! Send /start to begin.")
    logger.info("📱 Ready to download media with enhanced libraries!")
    logger.info("🎬 YouTube: PyTubeFix (fast) | 📸 Instagram: Instaloader | 🌐 Others: yt-dlp")
    
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
        raise

if __name__ == '__main__':
    main()