#!/usr/bin/env python3
"""
Caching server for cabin dashboard APIs.

Handles:
- JSON API responses (OWM, SNOTEL, NWS, UDOT) with 1-hour TTL
- WMS tile caching (Sentinel-2, weather radar) with daily file storage
- Graceful fallback to cached data on API failure

Usage:
    python3 cache_server.py [--port 8001] [--cache-dir ./cache]
"""

import http.server
import socketserver
import json
import time
import os
import urllib.request
import urllib.error
import urllib.parse
import sys
import argparse
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

# Default configuration
PORT = int(os.environ.get('PORT', 8080))
CACHE_DIR = os.environ.get('CACHE_DIR', './cache')
HOST = '0.0.0.0'

# API Configuration - loaded from environment variables
CABIN_LAT = float(os.environ.get('CABIN_LAT', 39.8918))
CABIN_LON = float(os.environ.get('CABIN_LON', -110.6824))
OWM_API_KEY = os.environ.get('OWM_API_KEY', '')
UDOT_API_KEY = os.environ.get('UDOT_API_KEY', '')
SENTINEL_HUB_CONFIG_ID = os.environ.get('SENTINEL_HUB_CONFIG_ID', '')
API_BASE_URL = os.environ.get('API_BASE_URL', '')  # e.g., https://api.cabin.moulderb.com

# Cache TTL in seconds (1 hour for JSON APIs)
CACHE_TTL = 3600

# In-memory cache for JSON responses
json_cache = {}


class CacheManager:
    """Manage file-based and memory-based caching"""

    def __init__(self, cache_dir):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.wms_dir = self.cache_dir / 'wms' / datetime.now().strftime('%Y-%m-%d')
        self.wms_dir.mkdir(parents=True, exist_ok=True)

    def get_wms_cache_path(self, cache_key, source='generic'):
        """Get file path for WMS tile cache, organized by source"""
        # Create subdirectory: cache/wms/{source}/{YYYY-MM-DD}
        today_dir = self.cache_dir / 'wms' / source / datetime.now().strftime('%Y-%m-%d')
        today_dir.mkdir(parents=True, exist_ok=True)

        # Hash the URL to create a safe filename
        filename = hashlib.md5(cache_key.encode()).hexdigest() + '.png'
        return today_dir / filename

    def get_wms_tile(self, url, source='generic'):
        """Get cached WMS tile or fetch from source (organized by source)"""
        cache_path = self.get_wms_cache_path(url, source)

        # Check if cached file exists
        if cache_path.exists():
            file_size = cache_path.stat().st_size
            print(f"   Cache: 📦 HIT ({file_size} bytes)")
            with open(cache_path, 'rb') as f:
                return f.read()

        # Fetch from source
        print(f"   Cache: 🔄 MISS - Fetching from source...")
        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'CabinDashboard/1.0')

            with urllib.request.urlopen(req, timeout=30) as response:
                data = response.read()

                # Save to cache
                cache_path.write_bytes(data)
                print(f"   Cache: 💾 SAVED ({len(data)} bytes to {source}/{cache_path.parent.name})")
                return data
        except Exception as e:
            print(f"   Cache: ❌ FETCH ERROR - {type(e).__name__}: {str(e)}")
            # Check if we have any cached tile (fallback to older dates)
            parent_dir = cache_path.parent.parent
            if parent_dir.exists():
                for date_dir in sorted(parent_dir.iterdir(), reverse=True)[:14]:
                    fallback_path = date_dir / cache_path.name
                    if fallback_path.exists():
                        print(f"   Cache: 🔄 FALLBACK using tile from {date_dir.name}")
                        return fallback_path.read_bytes()
            raise


class CachingRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler with caching logic"""

    cache_manager = None

    def end_headers(self):
        """Add CORS headers"""
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cross-Origin-Resource-Policy', 'cross-origin')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        """Handle POST requests for cache management"""
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {self.client_address[0]} - POST: {self.path[:80]}")

        if self.path.startswith('/api/clear-sentinel-cache'):
            self.handle_clear_sentinel_cache()
        else:
            self.send_response(404)
            self.end_headers()

    def handle_clear_sentinel_cache(self):
        """Clear old cached tiles for a specific Sentinel-2 layer"""
        parsed_path = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed_path.query)
        layer_name = params.get('layer', [None])[0]

        if not layer_name:
            self.send_response(400)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"layer parameter required")
            return

        try:
            sentinel_cache_dir = Path(CACHE_DIR) / 'wms' / 'sentinel-2'
            if sentinel_cache_dir.exists():
                cleared_count = 0
                # Remove all cached tiles for this layer
                for date_dir in sentinel_cache_dir.iterdir():
                    if date_dir.is_dir():
                        for tile_file in date_dir.glob('*.png'):
                            try:
                                tile_file.unlink()
                                cleared_count += 1
                            except Exception as e:
                                print(f"Error removing {tile_file}: {e}")

                print(f"🗑️  Cleared {cleared_count} cached Sentinel-2 tiles for {layer_name}")

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'status': 'success',
                    'cleared': cleared_count,
                    'layer': layer_name
                }).encode())
            else:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'status': 'success',
                    'cleared': 0,
                    'layer': layer_name
                }).encode())

        except Exception as e:
            print(f"❌ Error clearing cache: {e}")
            self.send_response(500)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(f"Error: {str(e)}".encode())

    def list_directory(self, path):
        self.send_response(403)
        self.end_headers()
        return None


    def serve_index_html(self):
        try:
            index_path = Path(__file__).parent / "index.html"

            if not index_path.exists():
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"index.html not found")
                return

            content = index_path.read_bytes()

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)

        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def do_GET(self):
        """Route requests to appropriate handlers"""

        # Log request with timestamp
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print()
        print(f"[{timestamp}] {self.client_address[0]} - REQUEST: {self.path[:80]}")

        # OpenWeatherMap proxy
        if self.path.startswith('/api/weather/owm'):
            self.handle_owm()

        # USDA SNOTEL proxy
        elif self.path.startswith('/api/weather/snotel'):
            self.handle_snotel()

        # USDA Soil Data proxy
        elif self.path.startswith('/api/weather/soil'):
            self.handle_soil()

        # NWS Alerts proxy
        elif self.path.startswith('/api/weather/nws-alerts'):
            self.handle_nws_alerts()

        # UDOT proxies
        elif self.path.startswith('/api/udot/cameras'):
            self.handle_udot_cameras()
        elif self.path.startswith('/api/udot/weather'):
            self.handle_udot_weather()
        elif self.path.startswith('/api/udot/roads'):
            self.handle_udot_roads()

        # Sentinel-2 WMS proxy
        elif self.path.startswith('/api/sentinel'):
            self.handle_sentinel()

        # Generic WMS proxy (USGS, ESRI, etc)
        elif self.path.startswith('/api/wms'):
            self.handle_wms()

        # Config endpoint - provides API keys to frontend
        elif self.path.startswith('/api/config'):
            self.handle_config()

        else:
            # Only serve index.html at root
            if self.path in ("/", "/index.html"):
                self.serve_index_html()
            else:
                self.send_response(404)
                self.end_headers()

    def handle_config(self):
        """Return configuration including API keys for frontend"""
        config = {
            'owm_api_key': OWM_API_KEY,
            'sentinel_hub_config_id': SENTINEL_HUB_CONFIG_ID,
            'cabin_lat': CABIN_LAT,
            'cabin_lon': CABIN_LON,
            'api_base_url': API_BASE_URL
        }
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(config).encode())

    def fetch_json_api(self, endpoint_name, url, timeout=30):
        """Fetch JSON API with caching and fallback logic - returns (data, status, is_cached)"""

        print(f"   Endpoint: {endpoint_name}")
        print(f"   URL: {url}")

        # Check memory cache
        now = time.time()
        if endpoint_name in json_cache:
            cached = json_cache[endpoint_name]
            age = now - cached['timestamp']

            if age < CACHE_TTL:
                print(f"   Status: ✅ SUCCESS (cache hit, age: {age:.0f}s)")
                return cached['data'], 200, True  # is_cached=True

        # Try to fetch fresh data
        try:
            print(f"   Status: 🔄 Fetching fresh data...")
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'CabinDashboard/1.0')

            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read().decode('utf-8')
                parsed = json.loads(data)

                # Update cache
                json_cache[endpoint_name] = {
                    'data': parsed,
                    'timestamp': now,
                    'last_successful': parsed
                }

                print(f"   Status: ✅ SUCCESS (fresh data, {len(data)} bytes)")
                return parsed, 200, False  # is_cached=False

        except Exception as e:
            print(f"   Status: ⚠️  FETCH FAILED - {type(e).__name__}: {str(e)}")

            # Fallback to last successful cached result
            if endpoint_name in json_cache and json_cache[endpoint_name].get('last_successful'):
                print(f"   Fallback: ✅ Using last cached result")
                return json_cache[endpoint_name]['last_successful'], 200, True  # is_cached=True (fallback)

            # No cached data available
            print(f"   Final Status: ❌ FAILED - No cached data available")
            return {'error': str(e)}, 500, False

    def send_json_response(self, data, status_code=200, is_cached=False):
        """Send JSON response with cache status header"""
        response = json.dumps(data).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('X-Cache-Status', 'HIT' if is_cached else 'MISS')
        self.send_header('Content-Length', len(response))
        self.end_headers()
        self.wfile.write(response)

    def handle_owm(self):
        """OpenWeatherMap proxy with caching"""
        print(f"🌡️  OpenWeatherMap request")

        url = (f'https://api.openweathermap.org/data/3.0/onecall'
               f'?appid={OWM_API_KEY}'
               f'&lat={CABIN_LAT}'
               f'&lon={CABIN_LON}'
               f'&exclude=minutely'
               f'&units=imperial')

        data, status, is_cached = self.fetch_json_api('owm', url)
        self.send_json_response(data, status, is_cached)

    def handle_snotel(self):
        """USDA SNOTEL proxy with caching - uses HOURLY for more current data"""
        print(f"❄️  USDA SNOTEL request")

        today = datetime.now().date()
        seven_days_ago = today - timedelta(days=7)

        params = (
            f'stationTriplets=1097:UT:SNTL'
            f'&elements=SNWD,TOBS,PREC,WTEQ,TAVG,TMAX,TMIN'
            f'&ordinal=1'
            f'&duration=HOURLY'
            f'&getFlags=false'
            f'&beginDate={seven_days_ago}'
            f'&endDate={today}'
        )
        url = f'https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data?{params}'

        data, status, is_cached = self.fetch_json_api('snotel', url, timeout=45)
        self.send_json_response(data, status, is_cached)

    def handle_soil(self):
        """USDA Soil Data proxy with caching - uses HOURLY for more current data"""
        print(f"🌱 USDA Soil Data request")

        today = datetime.now().date()
        seven_days_ago = today - timedelta(days=7)

        params = (
            f'stationTriplets=1097:UT:SNTL'
            f'&elements=SMS:-2,SMS:-8,SMS:-20,STO:-2,STO:-8,STO:-20'
            f'&ordinal=1'
            f'&duration=HOURLY'
            f'&getFlags=false'
            f'&beginDate={seven_days_ago}'
            f'&endDate={today}'
        )
        url = f'https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data?{params}'

        data, status, is_cached = self.fetch_json_api('soil', url, timeout=45)
        self.send_json_response(data, status, is_cached)

    def handle_nws_alerts(self):
        """NWS Alerts proxy with caching"""
        print(f"⚠️  NWS Alerts request")

        url = 'https://api.weather.gov/alerts/active?status=actual&message_type=alert'
        data, status, is_cached = self.fetch_json_api('nws_alerts', url, timeout=30)
        self.send_json_response(data, status, is_cached)

    def handle_udot_cameras(self):
        """UDOT Cameras proxy with caching"""
        print(f"📹 UDOT Cameras request")

        url = f'https://www.udottraffic.utah.gov/api/v2/get/cameras?key={UDOT_API_KEY}&format=json'
        data, status, is_cached = self.fetch_json_api('udot_cameras', url, timeout=30)
        self.send_json_response(data, status, is_cached)

    def handle_udot_weather(self):
        """UDOT Weather Stations proxy with caching"""
        print(f"📡 UDOT Weather request")

        url = f'https://www.udottraffic.utah.gov/api/v2/get/weatherstations?key={UDOT_API_KEY}&format=json'
        data, status, is_cached = self.fetch_json_api('udot_weather', url, timeout=30)
        self.send_json_response(data, status, is_cached)

    def handle_udot_roads(self):
        """UDOT Road Conditions proxy with caching"""
        print(f"🛣️  UDOT Roads request")

        url = f'https://www.udottraffic.utah.gov/api/v2/get/roadconditions?key={UDOT_API_KEY}&format=json'
        data, status, is_cached = self.fetch_json_api('udot_roads', url, timeout=30)
        self.send_json_response(data, status, is_cached)

    def cleanup_sentinel_cache(self, days=30):
        """Clean up Sentinel cache older than specified days (by imagery date)"""
        sentinel_cache_dir = Path(CACHE_DIR) / 'wms' / 'sentinel-2'
        if not sentinel_cache_dir.exists():
            return

        cutoff = datetime.now() - timedelta(days=days)
        cleaned_count = 0

        for date_dir in sentinel_cache_dir.iterdir():
            if date_dir.is_dir():
                try:
                    dir_date = datetime.strptime(date_dir.name, '%Y-%m-%d')
                    if dir_date < cutoff:
                        import shutil
                        shutil.rmtree(date_dir)
                        cleaned_count += 1
                        print(f"🗑️  Cleaned Sentinel cache: {date_dir.name}")
                except ValueError:
                    pass

        if cleaned_count > 0:
            print(f"🗑️  Removed {cleaned_count} Sentinel-2 cache folders older than {days} days")

    def handle_sentinel(self):
        """Sentinel-2 WMS proxy - forwards requests and caches tiles organized by imagery date"""
        print(f"🛰️  Sentinel-2 WMS request")

        # Get the original query string
        parsed_path = urllib.parse.urlparse(self.path)
        query_string = parsed_path.query

        # Build Sentinel Hub WMS URL
        sentinel_url = f'https://sh.dataspace.copernicus.eu/ogc/wms/{SENTINEL_HUB_CONFIG_ID}?{query_string}'

        # Generate cache key from URL
        cache_key = hashlib.md5(sentinel_url.encode()).hexdigest()

        try:
            # Check file cache first - look for any cached tile of this request
            tile_data = None
            cache_file_found = None
            sentinel_cache_dir = Path(CACHE_DIR) / 'wms' / 'sentinel-2'

            if sentinel_cache_dir.exists():
                # Look for any imagery date folder containing this tile
                for date_dir in sorted(sentinel_cache_dir.iterdir(), reverse=True):
                    if date_dir.is_dir():
                        check_file = date_dir / f'{cache_key}.png'
                        if check_file.exists():
                            with open(check_file, 'rb') as f:
                                tile_data = f.read()
                            cache_file_found = str(check_file)
                            print(f"   Status: ✅ CACHE HIT from {date_dir.name} ({len(tile_data)} bytes)")
                            break

            if tile_data is None:
                # Fetch from Sentinel Hub
                print(f"   Status: 🔄 Fetching from Copernicus...")
                req = urllib.request.Request(sentinel_url)
                req.add_header('User-Agent', 'CabinDashboard/1.0')

                with urllib.request.urlopen(req, timeout=30) as response:
                    tile_data = response.read()

                # Extract imagery date from query parameters
                params = urllib.parse.parse_qs(parsed_path.query)
                time_param = params.get('time', [''])[0]

                # Get the imagery date - use the end date from time range
                imagery_date = datetime.now().strftime('%Y-%m-%d')
                if time_param and '/' in time_param:
                    try:
                        end_date = time_param.split('/')[-1]
                        datetime.strptime(end_date, '%Y-%m-%d')  # Validate format
                        imagery_date = end_date
                    except:
                        pass

                # Cache to file organized by imagery date: ./cache/wms/sentinel-2/YYYY-MM-DD/hash.png
                cache_file = Path(CACHE_DIR) / 'wms' / 'sentinel-2' / imagery_date / f'{cache_key}.png'
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_file, 'wb') as f:
                    f.write(tile_data)

                print(f"   Status: ✅ SUCCESS ({len(tile_data)} bytes, cached by imagery date {imagery_date})")

                # Clean up old sentinel cache (keep only 30 days of imagery)
                self.cleanup_sentinel_cache(days=30)

            try:
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Cache-Control', 'max-age=86400')
                self.send_header('Content-Length', len(tile_data))
                self.end_headers()
                self.wfile.write(tile_data)
            except (BrokenPipeError, ConnectionResetError):
                # Client cancelled the request, ignore
                print(f"   Client disconnected during response")

        except Exception as e:
            print(f"   Status: ❌ FAILED - {type(e).__name__}: {str(e)}")
            try:
                self.send_response(500)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(f"Sentinel Error: {str(e)}".encode())
            except (BrokenPipeError, ConnectionResetError):
                # Client cancelled, ignore
                pass

    def handle_wms(self):
        """WMS proxy with file-based tile caching (365-day retention, organized by source)"""
        print(f"🗺️  WMS Tile request")

        # Parse query parameters
        parsed_path = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed_path.query)

        # Get source URL from query parameter
        source_url = params.get('url', [None])[0]

        print(f"   Source: {source_url[:80]}..." if source_url and len(source_url) > 80 else f"   Source: {source_url}")

        if not source_url:
            print(f"   Status: ❌ FAILED - No URL parameter provided")
            self.send_response(400)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"URL parameter required")
            return

        # Determine source from URL
        if 'basemap.nationalmap.gov' in source_url:
            source_name = 'usgs-topo'
        elif 'arcgisonline.com' in source_url:
            source_name = 'esri-imagery'
        else:
            source_name = 'generic-wms'

        try:
            # Get from file cache or fetch
            tile_data = self.cache_manager.get_wms_tile(source_url, source_name)

            print(f"   Status: ✅ SUCCESS ({len(tile_data)} bytes)")
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Cache-Control', 'max-age=31536000')  # 1 year
                self.send_header('Content-Length', len(tile_data))
                self.end_headers()
                self.wfile.write(tile_data)
            except (BrokenPipeError, ConnectionResetError):
                print(f"   Client disconnected during response")

        except Exception as e:
            print(f"   Status: ❌ FAILED - {type(e).__name__}: {str(e)}")
            try:
                self.send_response(500)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(f"WMS Error: {str(e)}".encode())
            except (BrokenPipeError, ConnectionResetError):
                pass



def cleanup_cache_monthly(cache_dir):
    """Clear all WMS cache on the 1st of the month"""
    today = datetime.now()

    # Only cleanup on the 1st of the month
    if today.day != 1:
        return

    cache_path = Path(cache_dir) / 'wms'
    if not cache_path.exists():
        return

    import shutil
    try:
        shutil.rmtree(cache_path)
        cache_path.mkdir(parents=True, exist_ok=True)
        print(f"🗑️  MONTHLY CACHE CLEAR: Removed all WMS cache on {today.strftime('%B %d, %Y')}")
    except Exception as e:
        print(f"❌ Error clearing cache: {e}")


def main():
    global CACHE_DIR, PORT

    parser = argparse.ArgumentParser(description='Cabin Dashboard Caching Server')
    parser.add_argument('--port', type=int, default=PORT, help=f'Port to listen on (default: {PORT})')
    parser.add_argument('--cache-dir', default=CACHE_DIR, help=f'Cache directory (default: {CACHE_DIR})')
    args = parser.parse_args()

    CACHE_DIR = args.cache_dir
    PORT = args.port

    # Setup cache manager
    CachingRequestHandler.cache_manager = CacheManager(CACHE_DIR)

    # Clean up cache on 1st of month (clear everything)
    cleanup_cache_monthly(CACHE_DIR)

    # Setup threading TCP server
    socketserver.TCPServer.allow_reuse_address = True
    Handler = CachingRequestHandler

    # Use ThreadingTCPServer for concurrent request handling
    class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True

    with ThreadedTCPServer((HOST, PORT), Handler) as httpd:
        print("╔" + "═" * 66 + "╗")
        print("║  🏔️  Cabin Dashboard Caching Server                            ║")
        print("╠" + "═" * 66 + "╣")
        print(f"║  Server running on port {PORT:<47} ║")
        print(f"║  Cache directory: {CACHE_DIR:<43} ║")
        print("║                                                                  ║")
        print("║  📡 API Endpoints:                                              ║")
        print(f"║     /api/weather/owm        - OpenWeatherMap                  ║")
        print(f"║     /api/weather/snotel     - USDA SNOTEL                     ║")
        print(f"║     /api/weather/soil       - USDA Soil Data                  ║")
        print(f"║     /api/weather/nws-alerts - NWS Alerts                      ║")
        print(f"║     /api/udot/cameras       - UDOT Cameras                    ║")
        print(f"║     /api/udot/weather       - UDOT Weather Stations           ║")
        print(f"║     /api/udot/roads         - UDOT Road Conditions            ║")
        print(f"║     /api/wms?url=...        - WMS Tile Proxy (file cached)    ║")
        print("║                                                                  ║")
        print("║  Cache Configuration:                                           ║")
        print(f"║     JSON APIs: 1-hour TTL (memory)                            ║")
        print(f"║     WMS Tiles: Daily (file-based)                             ║")
        print(f"║     Cleanup: Removes cache older than 7 days                  ║")
        print("║                                                                  ║")
        print("║  Press Ctrl+C to stop the server                              ║")
        print("╚" + "═" * 66 + "╝")
        print()

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n🛑 Shutting down server...")
            httpd.shutdown()


if __name__ == '__main__':
    main()
