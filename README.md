# Cabin Dashboard

A full-stack web dashboard for monitoring real-time weather, terrain, and road conditions around a cabin location in Utah. Combines a Python backend caching server with an interactive single-page frontend.

## Features

### Weather & Environmental Data
- **OpenWeatherMap** - Current conditions, hourly forecasts, and 7-day forecasts
- **USDA SNOTEL** - Snow depth, snow water equivalent, and temperature observations
- **USDA Soil Data** - Soil moisture and temperature at multiple depths
- **NWS Alerts** - National Weather Service alerts and warnings

### Infrastructure & Transportation
- **UDOT Traffic Cameras** - Live camera feeds from Utah Department of Transportation
- **UDOT Weather Stations** - Real-time weather readings from roadside stations
- **UDOT Road Conditions** - Current road status and advisories

### Mapping & Visualization
- **Sentinel-2 Satellite Imagery** - Copernicus satellite data via Sentinel Hub
- **Weather Radar Overlay** - Precipitation and radar data via WMS tiles
- **Interactive Map** - Leaflet-based map with multiple layer options
- **Historical Charts** - 7-day soil moisture, temperature, and snow depth trends

## Tech Stack

**Backend:**
- Python 3.11
- Built-in HTTP server with threading
- In-memory JSON cache + file-based WMS tile cache

**Frontend:**
- Vanilla JavaScript (no frameworks)
- Leaflet.js for mapping
- Chart.js for data visualization

**Infrastructure:**
- Docker & Docker Compose

## Quick Start

### Prerequisites
- Docker and Docker Compose
- API keys for:
  - [OpenWeatherMap](https://openweathermap.org/api)
  - [UDOT](https://www.udot.utah.gov/)
  - [Sentinel Hub](https://www.sentinel-hub.com/)

### Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/Cabin-Dashboard.git
   cd Cabin-Dashboard
   ```

2. Create a `.env` file from the example:
   ```bash
   cp .env.example .env
   ```

3. Edit `.env` with your API keys:
   ```
   OWM_API_KEY=your_openweathermap_api_key_here
   UDOT_API_KEY=your_udot_api_key_here
   SENTINEL_HUB_CONFIG_ID=your_sentinel_hub_config_id_here
   CABIN_LAT=39.8918
   CABIN_LON=-110.6824
   ```

4. Start the application:
   ```bash
   docker-compose up -d
   ```

5. Open your browser to `http://localhost:8080`

### Running Without Docker

```bash
python3 cache_server.py --port 8080 --cache-dir ./cache
```

Then open `cabin_dashboard_v2.html` in your browser.

## Project Structure

```
Cabin-Dashboard/
├── cabin_dashboard_v2.html   # Frontend single-page application
├── cache_server.py           # Backend caching proxy server
├── docker-compose.yml        # Docker Compose configuration
├── Dockerfile                # Docker image definition
├── .env.example              # Environment variables template
└── README.md
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `/api/weather/owm` | OpenWeatherMap weather data |
| `/api/weather/snotel` | USDA SNOTEL snow data |
| `/api/weather/soil` | USDA soil moisture/temperature |
| `/api/weather/nws-alerts` | NWS weather alerts |
| `/api/udot/cameras` | UDOT traffic camera locations |
| `/api/udot/weather` | UDOT weather station data |
| `/api/udot/roads` | UDOT road conditions |
| `/api/sentinel` | Sentinel-2 satellite tiles |
| `/api/wms` | Generic WMS tile proxy |
| `/api/config` | Server configuration |

## Caching Strategy

- **JSON APIs**: 1-hour in-memory cache with graceful fallback to stale data
- **WMS Tiles**: File-based daily cache organized by source and date
- **Sentinel-2 Images**: 14-day fallback window if current imagery unavailable
- Cache status returned via `X-Cache-Status` header (HIT/MISS)

## License

MIT
