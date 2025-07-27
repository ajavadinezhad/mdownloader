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
            #'youtube.com': 'YouTube',
            #'youtu.be': 'YouTube',
            'twitter.com': 'Twitter/X',
            'x.com': 'Twitter/X',
            'instagram.com': 'Instagram',
            'soundcloud.com': 'SoundCloud',
            #'tiktok.com': 'TikTok',
            #'facebook.com': 'Facebook',
            #'vimeo.com': 'Vimeo'
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
        
    async def download_via_subprocess(self, url, format_type, platform_name):
        """Download using direct yt-dlp subprocess call"""
        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_DIR)
        
        try:
            # Build the yt-dlp command
            cmd = ['yt-dlp']
            
            # Add cookies if available
            cookies_file = os.getenv('YTDLP_COOKIES')
            cookies_browser = os.getenv('YTDLP_COOKIES_BROWSER')
            
            if cookies_file and os.path.exists(cookies_file):
                cmd.extend(['--cookies', cookies_file])
                logger.info(f"✅ Using cookies from file: {cookies_file}")
            elif cookies_browser:
                cmd.extend(['--cookies-from-browser', cookies_browser])
                logger.info(f"✅ Using cookies from browser: {cookies_browser}")
            else:
                logger.warning("⚠️ No cookies configured")
            
            # Set format based on user choice and platform
            if format_type == 'audio':
                cmd.extend([
                    '--extract-audio',
                    '--audio-format', 'mp3',
                    '--audio-quality', '192K'
                ])
            else:
                # Video format - platform specific
                if 'youtube' in platform_name.lower():
                    cmd.extend(['--format', 'best[height<=720]/best'])
                elif 'instagram' in platform_name.lower():
                    cmd.extend(['--format', 'best[height<=1080]/best'])
                else:
                    cmd.extend(['--format', 'best'])
            
            # Output template
            output_template = os.path.join(temp_dir, '%(title)s.%(ext)s')
            cmd.extend(['--output', output_template])
            
            # Additional options
            cmd.extend([
                '--no-playlist',
                '--retries', '3',
                '--fragment-retries', '3',
                '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ])
            
            # Platform-specific options
            if 'instagram' in platform_name.lower():
                cmd.extend([
                    '--sleep-interval', '2',
                    '--max-sleep-interval', '5'
                ])
            elif 'youtube' in platform_name.lower():
                cmd.extend([
                    '--sleep-interval', '1',
                    '--max-sleep-interval', '3'
                ])
            
            # Add the URL
            cmd.append(url)
            
            logger.info(f"🚀 Running yt-dlp for {platform_name}: {url}")
            
            # Run the command
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
                cwd=temp_dir
            )
            
            if result.returncode == 0:
                # Success! Find the downloaded file
                files = os.listdir(temp_dir)
                if files:
                    filepath = os.path.join(temp_dir, files[0])
                    
                    # Check file size
                    file_size = os.path.getsize(filepath)
                    max_size_mb = MAX_FILE_SIZE // (1024 * 1024)
                    if file_size > MAX_FILE_SIZE:
                        return None, f"❌ File too large ({file_size // (1024*1024)}MB). Telegram limit is {max_size_mb}MB."
                    
                    logger.info(f"✅ {platform_name} download success: {os.path.basename(filepath)}")
                    return filepath, None
                else:
                    return None, "❌ Download completed but no file found"
            else:
                # Command failed
                error_output = result.stderr.strip()
                logger.error(f"❌ yt-dlp failed for {platform_name}: {error_output}")
                
                # Parse common errors for user-friendly messages
                if 'sign in' in error_output.lower() or 'bot' in error_output.lower():
                    return None, f"❌ {platform_name} detected automation. Try:\n• Different content\n• Wait 10-15 minutes\n• Public content works better"
                elif 'unavailable' in error_output.lower():
                    return None, "❌ Content unavailable. May be private, removed, or region-blocked."
                elif 'age' in error_output.lower() and 'restrict' in error_output.lower():
                    return None, "❌ Age-restricted content cannot be downloaded."
                elif 'private' in error_output.lower():
                    return None, "❌ This is private content."
                elif 'members-only' in error_output.lower():
                    return None, "❌ This is members-only content."
                elif 'login' in error_output.lower():
                    return None, f"❌ {platform_name} requires login. Content may be private."
                else:
                    return None, f"❌ {platform_name} download failed: {error_output[:100]}..."
        
        except subprocess.TimeoutExpired:
            return None, "❌ Download timed out (5 minutes). Content may be too large."
        except FileNotFoundError:
            return None, "❌ yt-dlp not found. Please install: pip install yt-dlp"
        except Exception as e:
            logger.error(f"❌ Subprocess error for {platform_name}: {e}")
            return None, f"❌ Error running yt-dlp: {str(e)}"
        
        finally:
            # Cleanup handled by calling function
            pass
    
    async def download_media(self, url, format_type='best'):
        """Download media - use instaloader for Instagram, subprocess for YouTube, yt-dlp for others"""
        platform_name = self.get_platform_name(url)
        
        # Use instaloader for Instagram
        if 'instagram.com' in url.lower():
            logger.info(f"📱 {platform_name} detected - using instaloader")
            return await self.download_instagram_with_instaloader(url, format_type)
        
        # Use subprocess for YouTube
        elif any(platform in url.lower() for platform in ['youtube.com', 'youtu.be']):
            logger.info(f"📱 {platform_name} detected - using direct yt-dlp subprocess")
            return await self.download_via_subprocess(url, format_type, platform_name)
        
        # Use yt-dlp library for other platforms
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
                # Twitter Strategy 1: Legacy API
                strategy1 = base_opts.copy()
                strategy1.update({
                    'format': 'best[height<=720]/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'DNT': '1',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                    },
                    'extractor_args': {
                    }
                })
                strategies.append(strategy1)
                
                # Twitter Strategy 2: Syndication API
                strategy2 = base_opts.copy()
                strategy2.update({
                    'format': 'best[height<=480]/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
                        'Accept': '*/*',
                        'Accept-Language': 'en-US,en;q=0.5',
                    },
                    'extractor_args': {
                        'twitter': {
                            'api': 'syndication',
                        }
                    }
                })
                strategies.append(strategy2)
            elif 'tiktok.com' in url:
                # Enhanced TikTok strategies
                logger.info("📱 TikTok using enhanced yt-dlp strategies")
                
                # TikTok Strategy 1: Mobile browser
                strategy1 = base_opts.copy()
                strategy1.update({
                    'format': 'best[height<=720]/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Referer': 'https://www.tiktok.com/',
                        'Sec-Fetch-Dest': 'document',
                        'Sec-Fetch-Mode': 'navigate',
                    },
                    'sleep_interval': 2,
                    'max_sleep_interval': 5,
                })
                strategies.append(strategy1)
                
                # TikTok Strategy 2: Desktop browser
                strategy2 = base_opts.copy()
                strategy2.update({
                    'format': 'best[height<=480]/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Referer': 'https://www.tiktok.com/',
                        'DNT': '1',
                    },
                    'sleep_interval': 1,
                })
                strategies.append(strategy2)
                
            else:
                # Default strategy for other platforms (SoundCloud, TikTok, etc.)
                strategy = base_opts.copy()
                strategy.update({
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    }
                })
                
                # Platform-specific format adjustments
                if 'soundcloud.com' in url:
                    strategy['format'] = 'bestaudio/best'
                    if format_type == 'audio':
                        strategy['postprocessors'] = [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192',
                        }]
                elif 'facebook.com' in url:
                    strategy['format'] = 'best[height<=720]'
                elif 'vimeo.com' in url:
                    strategy['format'] = 'best[height<=1080]'
                
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
                            
                            # Handle specific errors
                            if any(keyword in error_msg for keyword in ['sign in', 'bot', 'automated']):
                                if i < len(strategies) - 1:
                                    continue
                                return None, f"❌ {platform_name} detected automated access. Try different content."
                            elif any(keyword in error_msg for keyword in ['private', 'unavailable', 'not found']):
                                if i < len(strategies) - 1:
                                    continue
                                return None, "❌ Content is private, unavailable, or was removed."
                            elif any(keyword in error_msg for keyword in ['age', 'restricted']):
                                return None, "❌ Age-restricted content cannot be downloaded."
                            else:
                                if i < len(strategies) - 1:
                                    continue
                                return None, f"❌ Error accessing content: {str(e)[:100]}..."
                        
                        title = info.get('title', 'Unknown')
                        duration = info.get('duration', 0)
                        
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
                            error_msg = str(e).lower()
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
            logger.error(f"Unexpected error in download_media: {e}")
            return None, f"❌ Unexpected error: {str(e)[:100]}..."
        
        finally:
            # Cleanup happens in the calling function
            pass

# Initialize bot
bot = MediaDownloaderBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    max_size_mb = MAX_FILE_SIZE // (1024*1024)
    welcome_text = f"""
🎬 <b>Media Downloader Bot</b>

Send me a URL from any of these platforms and I'll download it for you:

📱 <b>Supported Platforms:</b>
• Twitter/X (videos) 🐦
• Instagram (posts & reels) 📸
• SoundCloud (audio) 🎧

📝 <b>How to use:</b>
1. Send me a URL
2. Choose video or audio format
3. I'll download and send it to you!

⚠️ <b>Note:</b> Files must be under {max_size_mb}MB due to Telegram limits.

🚀 <b>Enhanced:</b> YouTube uses optimized downloading and Instagram uses instaloader for better success rates!
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
        # YouTube - enhanced with subprocess
        keyboard = [
            [
                InlineKeyboardButton("🎥 Video (Enhanced)", callback_data=f"video|{url_id}"),
                InlineKeyboardButton("🎵 Audio (Enhanced)", callback_data=f"audio|{url_id}")
            ]
        ]
        format_text = "Choose download format (Enhanced for better success):"
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
    enhanced_platforms = ['YouTube', 'Instagram']
    
    if platform == 'Instagram':
        download_msg = f"{format_emoji} Downloading {format_type} from {platform} (Instaloader)...\n\n⏳ Using specialized Instagram downloader for better reliability."
    elif platform == 'YouTube':
        download_msg = f"{format_emoji} Downloading {format_type} from {platform} (Enhanced)...\n\n⏳ Using optimized method for better success rate."
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
                if platform == 'YouTube':
                    error_text += "\n\n💡 Enhanced method tried but failed. Suggestions:\n"
                    error_text += "• Try different content from the same platform\n"
                    error_text += "• Wait 10-15 minutes before trying again\n"
                    error_text += "• Public content works better than private/restricted"
                else:
                    error_text += "\n\n💡 Suggestions:\n"
                    error_text += "• Try different content\n"
                    error_text += "• Wait 10-15 minutes before trying again"
            elif "private" in error.lower() or "unavailable" in error.lower():
                if platform == 'Instagram':
                    error_text += "\n\n💡 Instagram Tips:\n"
                    error_text += "• Make sure the account is public\n"
                    error_text += "• Check if the post still exists\n"
                    error_text += "• Some accounts may require login"
                else:
                    error_text += "\n\n💡 Try: Make sure the content is public and available in your region"
            elif "age-restricted" in error.lower():
                error_text += "\n\n💡 Note: Age-restricted content cannot be downloaded"
            elif "timed out" in error.lower():
                error_text += "\n\n💡 Try: Content might be too large. Try shorter videos or audio format"
            
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
            success_msg += " (Enhanced method succeeded!)"
        
        await query.edit_message_text(success_msg)
        
        # Prepare caption
        if platform == 'Instagram':
            caption = f"🎵 Downloaded from {platform} (Instaloader)" if format_type == 'audio' else f"🎥 Downloaded from {platform} (Instaloader)"
        elif platform == 'YouTube':
            caption = f"🎵 Downloaded from {platform} (Enhanced)" if format_type == 'audio' else f"🎥 Downloaded from {platform} (Enhanced)"
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
            final_msg += f"\n🚀 Enhanced method used for {platform}"
        
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
🆘 Help - Media Downloader Bot

📝 How to download:
1. Copy a URL from supported platforms
2. Send it to this bot
3. Choose video or audio format
4. Wait for download and upload

📱 Supported Platforms:
• Twitter/X - Videos and GIFs 🐦
• Instagram - Posts, Reels, Images 📸
• SoundCloud - Audio tracks 🎧

🚀 Enhanced Platforms:
• YouTube uses optimized downloading with direct yt-dlp subprocess calls for better success rates and cookie support
• Instagram uses instaloader library for reliable access to posts and reels

⚠️ Limitations:
• Maximum file size: {max_size_mb}MB
• Audio is converted to MP3 format
• Some private or age-restricted content may not work

💡 Tips:
• YouTube has enhanced success rates with subprocess method
• Instagram uses instaloader for better reliability and no browser requirements
• Public content works better than private
• Educational content typically works better than viral content
• If download fails, try waiting 10-15 minutes and retry
• Different content from the same platform may work better

🍪 Cookie Support:
The bot supports YouTube cookies for better access to content. Instagram uses instaloader which doesn't require cookies.
    """
    
    await update.message.reply_text(help_text)

def main():
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("❌ Bot token not configured!")
        return
    
    logger.info("🤖 Initializing Enhanced Media Downloader Bot...")
    logger.info(f"📁 Download directory: {DOWNLOAD_DIR}")
    logger.info(f"📏 Max file size: {MAX_FILE_SIZE // (1024*1024)}MB")
    
    # Check cookie configuration
    cookies_file = os.getenv('YTDLP_COOKIES')
    cookies_browser = os.getenv('YTDLP_COOKIES_BROWSER')
    
    if cookies_file and os.path.exists(cookies_file):
        logger.info(f"🍪 YouTube cookies configured: {cookies_file}")
    elif cookies_browser:
        logger.info(f"🍪 Browser cookies configured: {cookies_browser}")
    else:
        logger.warning("⚠️ No YouTube cookies configured - some downloads may fail")
    
    # Check if yt-dlp is available
    try:
        result = subprocess.run(['yt-dlp', '--version'], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            version = result.stdout.strip()
            logger.info(f"✅ yt-dlp available: {version}")
        else:
            logger.warning("⚠️ yt-dlp command failed")
    except FileNotFoundError:
        logger.error("❌ yt-dlp not found! Install with: pip install yt-dlp")
        return
    except Exception as e:
        logger.warning(f"⚠️ Could not check yt-dlp: {e}")
    
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(handle_format_selection))
    
    # Run the bot until the user presses Ctrl-C
    logger.info("🚀 Enhanced Bot is running! Send /start to begin.")
    logger.info("📱 Ready to download media with enhanced support!")
    logger.info("🎯 YouTube: Enhanced yt-dlp subprocess | Instagram: Instaloader library")
    
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
        raise

if __name__ == '__main__':
    main()