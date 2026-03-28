# penplotter/web

Web interface for controlling an AxiDraw pen plotter from a Raspberry Pi.

![Main interface](docs/main.png)
![Configuration](docs/config.png)

## Setup

```bash
./run.sh          # HTTPS on port 4443
```

Or as a systemd service:

```bash
# Edit penplotter-web.service.example with your user/paths, then:
cp docs/penplotter-web.service.example penplotter-web.service
sudo cp penplotter-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now penplotter-web
```

Access at `https://<your-hostname>:4443`

## Features

- Upload, select, preview SVG files
- Plot with layer mode support (Inkscape layers)
- Pause / Resume / Stop with Home return
- Pen up/down, XY move controls
- Estimate plot time before starting
- Live progress with ETA
- Configuration UI (model, pen positions, speed, path optimization)
- Web notifications on plot complete/pause/error
- Real-time log with SSE

## Requirements

- Python 3.9+
- `axicli` installed and in PATH
- AxiDraw connected via USB

## Home Assistant Integration

A custom integration is included for [HACS](https://hacs.xyz/).

### Install via HACS

1. HACS → **Custom repositories** → add this repo as **Integration**
2. Download "Pen Plotter (AxiDraw)"
3. Restart Home Assistant
4. **Settings → Devices & Services → Add Integration → Pen Plotter**
5. Enter your hostname (e.g. `rasp4blue.local`) and port (`4443`)

![Setup step 1](docs/ha-setup-1.png)
![Setup step 2](docs/ha-setup-2.png)

### Exposed entities

| Entity | Type | Description |
|--------|------|-------------|
| Pen Plotter State | Sensor | Current state (`idle`, `plotting`, `paused`, `error`) with attributes: `current_file`, `elapsed`, `can_home`, `error` |
| Pen Plotter Progress | Sensor | Plot progress in % |
| Pen Plotter Pause | Button | Pause current plot |
| Pen Plotter Resume | Button | Resume paused plot |
| Pen Plotter Stop | Button | Stop and cancel plot |
| Pen Plotter Pen Up | Button | Raise pen |
| Pen Plotter Pen Down | Button | Lower pen |
| Pen Plotter Home | Button | Return to home position |

![Device state](docs/ha-state.png)
![Dashboard](docs/ha-dashboard.png)

Status is polled every 15 seconds. The server must be running with HTTPS. If using self-signed certificates, uncheck "Verify SSL certificate" during setup.

## Disclaimer

This software is provided as-is. Use at your own risk. The author is not responsible for any damage to your hardware, artwork, or anything else resulting from the use of this software. See [LICENSE](LICENSE).

