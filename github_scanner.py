import requests
from urllib.parse import urljoin
import time
import os
import re
import threading
import concurrent.futures
import argparse
import signal
from datetime import datetime

# ============================================================
#  CONFIGURATION
# ============================================================

FRONTEND_URL = "http://10.15.223.139/"
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "BEATBOX_LINKS")

# 🔥 DISCORD WEBHOOK
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1553665610666344591/9Au8Z7WU3gEbzD9T-fngsSJkvp8RziyO1u2EpTYW6tEWld6pWz2X6VY6wtlnTSER-WXi"

MAX_WORKERS = 16
MAX_RETRIES = 2
TIMEOUT = 8

PREVIEW_START = 1
PREVIEW_END = 43500

EXTENSIONS = (
    '.mp4', '.mkv', '.iso', '.mp3', '.avi', '.mpg', '.flac', '.wav',
    '.exe', '.zip', '.rar', '.7z', '.m4a', '.m4v', '.wmv', '.mov',
    '.mpeg', '.bin', '.cue', '.img', '.nrg', '.mp2', '.ogg', '.wma',
    '.aac', '.mka', '.flv', '.webm', '.3gp', '.ts', '.m2ts', '.mts'
)

URL_PATTERN = re.compile(
    r'https?://[^\s"\'<>]+\.(?:' + '|'.join(ext.lstrip('.') for ext in EXTENSIONS) + r')',
    re.IGNORECASE
)

# ============================================================
#  DISCORD SENDER
# ============================================================

class DiscordSender:
    def __init__(self, webhook_url):
        self.webhook_url = webhook_url
        self.lock = threading.Lock()

    def send_file(self, filepath, category, count):
        """Send txt file to Discord webhook"""
        if not os.path.exists(filepath):
            print(f"   ⚠️ File not found: {filepath}")
            return False

        filename = os.path.basename(filepath)
        
        try:
            with open(filepath, 'rb') as f:
                files = {
                    'file': (filename, f, 'text/plain')
                }
                payload = {
                    'content': f"📂 **{category.upper()}** — `{count}` links\n📁 File: `{filename}`\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                }
                
                r = requests.post(self.webhook_url, data=payload, files=files, timeout=30)
                
                if r.status_code in (200, 204):
                    print(f"   ✅ Sent to Discord: {filename} ({count} links)")
                    return True
                else:
                    print(f"   ❌ Discord error: {r.status_code} - {r.text[:100]}")
                    return False
        except Exception as e:
            print(f"   ❌ Discord exception: {e}")
            return False

    def send_message(self, message):
        """Send plain text message to Discord"""
        try:
            r = requests.post(self.webhook_url, json={'content': message}, timeout=15)
            return r.status_code in (200, 204)
        except Exception:
            return False


# ============================================================
#  SCRAPER
# ============================================================

class FastScraper:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.all_links = {
            'movies': set(),
            'games': set(),
            'music': set(),
            'software': set(),
            'tvshows': set(),
            'wallpapers': set(),
            'videos': set(),
            'other': set()
        }
        self.lock = threading.Lock()
        self.running = True
        self.processed = 0
        self.found_count = 0
        self.start_time = time.time()
        self.last_update = time.time()
        
        # Track which categories have been sent
        self.sent_categories = set()
        
        # Discord sender
        self.discord = DiscordSender(DISCORD_WEBHOOK)
        
        # Create output directory
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # Session
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=MAX_WORKERS,
            pool_maxsize=MAX_WORKERS,
            max_retries=0
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        })
        
        signal.signal(signal.SIGINT, self.signal_handler)

    def signal_handler(self, sig, frame):
        print("\n\n⚠️ Stopping... Saving & Sending collected links...")
        self.running = False

    def categorize(self, url):
        u = url.lower()
        if '/englishmovies/' in u or '/hindimovies/' in u or 'hollywood' in u or 'bollywood' in u or 'wrestling' in u:
            return 'movies'
        elif '/games' in u or '/racing/' in u or '/action/' in u:
            return 'games'
        elif '/music/' in u or '/mp3/' in u or '/song/' in u:
            return 'music'
        elif '/software' in u:
            return 'software'
        elif '/tvshow' in u or '/season' in u or '/series' in u:
            return 'tvshows'
        elif '/wallpaper' in u or '/image/' in u or '/photo/' in u:
            return 'wallpapers'
        elif '/video/' in u:
            return 'videos'
        return 'other'

    def extract_links(self, html):
        found = set()
        for match in URL_PATTERN.finditer(html):
            found.add(match.group(0))
        
        src_pattern = re.compile(
            r'src=["\']([^"\']+\.(?:' + '|'.join(ext.lstrip('.') for ext in EXTENSIONS) + r'))["\']',
            re.IGNORECASE
        )
        for match in src_pattern.finditer(html):
            url = match.group(1)
            if not url.startswith('http'):
                url = urljoin(FRONTEND_URL, url)
            found.add(url)
        
        return found

    def save_category_file(self, category):
        """Save a single category's links to its own txt file and send to Discord"""
        links = self.all_links[category]
        if not links:
            return
        
        filename = f"BEATBOX_{category.upper()}.txt"
        filepath = os.path.join(OUTPUT_DIR, filename)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("="*80 + "\n")
                f.write(f"  🎯 BEAT BOX - {category.upper()}\n")
                f.write(f"  📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"  📊 Total Links: {len(links)}\n")
                f.write("="*80 + "\n\n")
                
                for link in sorted(links):
                    f.write(f"{link}\n")
                
                f.write("\n" + "="*80 + "\n")
            
            print(f"\n   💾 Saved: {filename} ({len(links)} links)")
            
            # Send to Discord
            self.discord.send_file(filepath, category, len(links))
            
        except Exception as e:
            print(f"   ❌ Save error for {category}: {e}")

    def flush_completed_categories(self):
        """Check & send any completed categories"""
        with self.lock:
            for category in self.all_links.keys():
                if category in self.sent_categories:
                    continue
                # Send if there are links (even partial)
                if self.all_links[category]:
                    self.sent_categories.add(category)
                    # Release lock before sending
                    links_copy = list(self.all_links[category])
        
        # Send outside lock
        for category in list(self.sent_categories):
            if category in self.sent_categories:
                self.save_category_file(category)

    def scrape_one(self, movie_id):
        if not self.running:
            return
        
        url = f"{FRONTEND_URL}classic/moviepreview-{movie_id}.html"
        
        try:
            response = self.session.get(url, timeout=TIMEOUT)
            
            if response.status_code == 200:
                links = self.extract_links(response.text)
                
                if links:
                    with self.lock:
                        for link in links:
                            category = self.categorize(link)
                            if link not in self.all_links[category]:
                                self.all_links[category].add(link)
                                self.found_count += 1
                    
                    if self.verbose:
                        print(f"✅ ID {movie_id}: {len(links)} link(s)")
                        
        except Exception:
            pass
        
        with self.lock:
            self.processed += 1
            now = time.time()
            if now - self.last_update >= 1.0:
                self.print_progress()
                self.last_update = now

    def print_progress(self):
        if not self.running:
            return
        
        total = PREVIEW_END - PREVIEW_START + 1
        percent = (self.processed / total) * 100
        elapsed = time.time() - self.start_time
        
        if self.processed > 0:
            speed = self.processed / elapsed
            eta_seconds = (total - self.processed) / speed if speed > 0 else 0
            eta_min = int(eta_seconds // 60)
            eta_sec = int(eta_seconds % 60)
            
            bar_length = 30
            filled = int(bar_length * self.processed / total)
            bar = '█' * filled + '░' * (bar_length - filled)
            
            print(f"\r[{bar}] {percent:5.1f}% | "
                  f"{self.processed}/{total} | "
                  f"🔗 {self.found_count} links | "
                  f"⚡ {speed:.1f}/s | "
                  f"⏱️ ETA: {eta_min}m {eta_sec}s   ", end='', flush=True)

    def scrape_all(self, start_id, end_id):
        global PREVIEW_START, PREVIEW_END
        PREVIEW_START = start_id
        PREVIEW_END = end_id
        
        total = end_id - start_id + 1
        
        print("""
╔══════════════════════════════════════════════════════════╗
║   🎯 BEAT BOX - FASTEST LINK GRABBER v4.0               ║
║   With Discord Auto-Send                                ║
╚══════════════════════════════════════════════════════════╝
        """)
        print(f"📡 Target: {FRONTEND_URL}classic/moviepreview-*.html")
        print(f"🔢 Range: {start_id} to {end_id} ({total:,} pages)")
        print(f"⚡ Threads: {MAX_WORKERS}")
        print(f"💾 Output: {OUTPUT_DIR}")
        print(f"🔔 Discord: Enabled")
        print(f"⏱️  Started: {datetime.now().strftime('%H:%M:%S')}")
        print("="*60)
        print("Press Ctrl+C anytime to stop & save\n")
        
        # Send start notification
        self.discord.send_message(
            f"🚀 **Beat Box Scraper Started**\n"
            f"📊 Range: `{start_id}` to `{end_id}`\n"
            f"⚡ Threads: `{MAX_WORKERS}`\n"
            f"⏰ Started: `{datetime.now().strftime('%H:%M:%S')}`"
        )
        
        # 🔥 INTERVAL-BASED SENDING (har 5 minute baad)
        last_send_time = time.time()
        SEND_INTERVAL = 300  # 5 minutes = 300 seconds
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = []
            for movie_id in range(start_id, end_id + 1):
                if not self.running:
                    break
                futures.append(executor.submit(self.scrape_one, movie_id))
            
            # Wait with periodic sends
            while futures:
                done, futures = concurrent.futures.wait(
                    futures, timeout=10,
                    return_when=concurrent.futures.FIRST_COMPLETED
                )
                
                # Check interval
                now = time.time()
                if now - last_send_time >= SEND_INTERVAL:
                    print(f"\n⏰ Interval reached! Sending intermediate results...")
                    self.send_all_categories(final=False)
                    last_send_time = now
                
                if not self.running:
                    break
        
        print()
        self.print_progress()
        print()

    def send_all_categories(self, final=True):
        """Send all categories that have links (to Discord)"""
        print("\n" + "="*60)
        print(f"📤 Sending {'FINAL' if final else 'INTERMEDIATE'} files to Discord...")
        print("="*60)
        
        for category in ['movies', 'games', 'music', 'software', 'tvshows', 'wallpapers', 'videos', 'other']:
            links = self.all_links[category]
            if links:
                self.save_category_file(category)

    def save_links(self, final=True):
        """Save all collected links"""
        print("\n" + "="*60)
        print("💾 Saving & Sending results...")
        
        total = sum(len(v) for v in self.all_links.values())
        elapsed = time.time() - self.start_time
        
        # Save & send each category separately
        self.send_all_categories(final=final)
        
        # Also save a master file
        try:
            master_file = os.path.join(OUTPUT_DIR, "BEATBOX_ALL_LINKS.txt")
            with open(master_file, 'w', encoding='utf-8') as f:
                f.write("="*80 + "\n")
                f.write("  🎯 BEAT BOX - ALL LINKS\n")
                f.write(f"  📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"  ⏱️  Total Time: {int(elapsed//60)}m {int(elapsed%60)}s\n")
                f.write(f"  📊 Pages Scanned: {self.processed}\n")
                f.write("="*80 + "\n\n")
                
                for category in ['movies', 'games', 'music', 'software', 'tvshows', 'wallpapers', 'videos', 'other']:
                    links = self.all_links[category]
                    if links:
                        f.write(f"\n{'█'*60}\n")
                        f.write(f"  📂 {category.upper()} ({len(links)} links)\n")
                        f.write(f"{'█'*60}\n\n")
                        for link in sorted(links):
                            f.write(f"{link}\n")
                        f.write("\n")
                
                f.write("\n" + "="*80 + "\n")
                f.write(f"  ✅ TOTAL LINKS: {total}\n")
                f.write("="*80 + "\n")
            
            print(f"\n✅ Master file saved: {master_file}")
            
            # Send master file to Discord
            self.discord.send_file(master_file, "ALL_CATEGORIES", total)
            
            # Send summary message
            self.discord.send_message(
                f"✅ **Beat Box Scraper Completed!**\n\n"
                f"📊 **Total Links:** `{total}`\n"
                f"📄 **Pages Scanned:** `{self.processed}`\n"
                f"⏱️ **Time:** `{int(elapsed//60)}m {int(elapsed%60)}s`\n"
                f"📁 **Files Sent:** `{len([c for c in self.all_links if self.all_links[c]])}` categories\n\n"
                f"🎉 **All Done!**"
            )
            
        except Exception as e:
            print(f"\n❌ Save error: {e}")
        
        print(f"\n✅ DONE!")
        print(f"📊 Total links found: {total}")
        print(f"📁 Saved at: {OUTPUT_DIR}")
        print(f"⏱️  Time taken: {int(elapsed//60)}m {int(elapsed%60)}s")
        print("\n🎉 All files sent to Discord!")
        return True

    def run(self, start_id, end_id):
        try:
            self.scrape_all(start_id, end_id)
            return self.save_links(final=True)
        except KeyboardInterrupt:
            print("\n\n⚠️ Interrupted! Saving & Sending...")
            return self.save_links(final=True)
        except Exception as e:
            print(f"\n❌ Error: {e}")
            return self.save_links(final=True)


# ============================================================
#  MAIN
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(description='Beat Box Fastest Link Grabber v4.0')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Show every found link')
    parser.add_argument('--start', type=int, default=PREVIEW_START,
                       help=f'Start ID (default: {PREVIEW_START})')
    parser.add_argument('--end', type=int, default=PREVIEW_END,
                       help=f'End ID (default: {PREVIEW_END})')
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    scraper = FastScraper(verbose=args.verbose)
    scraper.run(start_id=args.start, end_id=args.end)
