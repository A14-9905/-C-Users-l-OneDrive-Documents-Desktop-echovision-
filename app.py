from flask import Flask, jsonify, render_template_string, request
from gpiozero import Button
import serial
import pynmea2
import threading
import time
import requests
import subprocess
import math

app = Flask(__name__)

# =========================================================
# CONFIG
# =========================================================
GPS_PORT = "/dev/ttyAMA0"
GPS_BAUD = 9600
SOS_BUTTON_PIN = 22

# Telegram
BOT_TOKEN = "bot token"
CHAT_ID = "chat id"

# Google Routes API
GOOGLE_ROUTES_API_KEY = "api key"

# USB speaker ALSA device
# Find using: aplay -l
# Example:
# USB_AUDIO_DEVICE = "plughw:2,0"
USB_AUDIO_DEVICE = "plughw:2,0"

NAV_REPEAT_COOLDOWN = 10.0
SOS_ALERT_SECONDS = 20
STEP_REACHED_DISTANCE_M = 20

# =========================================================
# GLOBAL STATE
# =========================================================
latest_data = {
    "lat": None,
    "lon": None,
    "last_update": "Waiting for GPS...",
    "danger": False,
    "danger_message": "SAFE",
    "destination": "",
    "route_active": False,
    "current_instruction": "No route loaded",
    "distance_to_next_m": None,
    "next_step_index": 0,
    "steps": [],
    "maps_link": "",
    "sos_message": "",
    "browser_alert_sound": False
}

gps_lock = threading.Lock()
route_lock = threading.Lock()
danger_lock = threading.Lock()

last_nav_spoken_time = 0
danger_until = 0

# =========================================================
# DEVICES
# =========================================================
ser = serial.Serial(GPS_PORT, GPS_BAUD, timeout=1)
sos_button = Button(SOS_BUTTON_PIN, pull_up=True, bounce_time=0.35)

# =========================================================
# HELPERS
# =========================================================
def speak_navigation(text: str):
    """
    Speak through USB speaker using ALSA device.
    Falls back to default speaker if USB device fails.
    """
    try:
        espeak = subprocess.Popen(
            ["espeak-ng", "-s", "145", "--stdout", text],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )

        aplay = subprocess.Popen(
            ["aplay", "-D", USB_AUDIO_DEVICE],
            stdin=espeak.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        if espeak.stdout:
            espeak.stdout.close()

    except Exception as e:
        print("USB speech error, using default output:", e)
        try:
            subprocess.Popen(
                ["espeak-ng", "-s", "145", text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e2:
            print("Speech fallback error:", e2)

def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))

def send_telegram(message: str):
    try:
        if BOT_TOKEN.startswith("bot token") or CHAT_ID.startswith(chat id):
            print("Telegram not configured")
            return False

        url = f"https://api.telegram.org/bot{api}/sendMessage"
        r = requests.get(
            url,
            params={
                "chat_id": CHAT_ID,
                "text": message
            },
            timeout=10
        )

        print("Telegram response:", r.text)
        return r.status_code == 200

    except Exception as e:
        print("Telegram error:", e)
        return False

def build_sos_message():
    with gps_lock:
        lat = latest_data["lat"]
        lon = latest_data["lon"]

    if lat is not None and lon is not None:
        maps_link = f"https://www.google.com/maps?q={lat},{lon}"
        msg = f"Person is in danger. Current GPS location: {lat}, {lon}. Map: {maps_link}"
    else:
        maps_link = ""
        msg = "Person is in danger. GPS location not available."

    return msg, maps_link

def parse_navigation_steps(route_json):
    steps = []
    try:
        legs = route_json["routes"][0]["legs"]
        for leg in legs:
            for step in leg.get("steps", []):
                nav = step.get("navigationInstruction", {})
                instruction = nav.get("instructions", "Continue")
                end_loc = step.get("endLocation", {}).get("latLng", {})
                distance_m = int(step.get("distanceMeters", 0))

                if "latitude" in end_loc and "longitude" in end_loc:
                    steps.append({
                        "instruction": instruction,
                        "lat": end_loc["latitude"],
                        "lon": end_loc["longitude"],
                        "distance_m": distance_m
                    })
    except Exception as e:
        print("Step parse error:", e)

    return steps

def request_route(origin_lat, origin_lon, destination_text):

    url = "https://routes.googleapis.com/directions/v2:computeRoutes"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_ROUTES_API_KEY,
        "X-Goog-FieldMask": (
            "routes.legs.steps.navigationInstruction.instructions,"
            "routes.legs.steps.distanceMeters,"
            "routes.legs.steps.endLocation,"
            "routes.distanceMeters,routes.duration"
        )
    }

    body = {
        "origin": {
            "location": {
                "latLng": {
                    "latitude": origin_lat,
                    "longitude": origin_lon
                }
            }
        },
        "destination": {
            "address": destination_text
        },
        "travelMode": "WALK",
        "languageCode": "en-US",
        "units": "METRIC"
    }

    r = requests.post(url, headers=headers, json=body, timeout=20)

    if r.status_code != 200:
        print("Google Routes error:", r.text)
        raise Exception(f"Google Routes API failed: {r.text}")

    return r.json()
# =========================================================
# GPS LOOP
# =========================================================
def gps_loop():
    while True:
        try:
            line = ser.readline().decode("ascii", errors="replace").strip()
            if not line:
                continue

            if line.startswith("$GNGGA") or line.startswith("$GNRMC") or \
               line.startswith("$GPGGA") or line.startswith("$GPRMC"):

                msg = pynmea2.parse(line)

                if hasattr(msg, "latitude") and hasattr(msg, "longitude"):
                    lat = msg.latitude
                    lon = msg.longitude

                    if lat and lon and lat != 0.0 and lon != 0.0:
                        with gps_lock:
                            latest_data["lat"] = round(lat, 6)
                            latest_data["lon"] = round(lon, 6)
                            latest_data["last_update"] = time.strftime("%H:%M:%S")

        except Exception as e:
            print("GPS error:", e)
            time.sleep(1)

# =========================================================
# NAVIGATION LOOP
# =========================================================
def navigation_loop():
    global last_nav_spoken_time

    while True:
        try:
            with route_lock:
                route_active = latest_data["route_active"]
                steps = list(latest_data["steps"])
                idx = latest_data["next_step_index"]

            if not route_active or not steps or idx >= len(steps):
                time.sleep(1)
                continue

            with gps_lock:
                lat = latest_data["lat"]
                lon = latest_data["lon"]

            if lat is None or lon is None:
                time.sleep(1)
                continue

            step = steps[idx]
            dist = haversine_m(lat, lon, step["lat"], step["lon"])

            with route_lock:
                latest_data["distance_to_next_m"] = int(dist)
                latest_data["current_instruction"] = step["instruction"]

            now = time.time()

            if dist <= STEP_REACHED_DISTANCE_M:
                speak_navigation(step["instruction"])
                time.sleep(1)

                with route_lock:
                    latest_data["next_step_index"] += 1

                    if latest_data["next_step_index"] >= len(latest_data["steps"]):
                        latest_data["route_active"] = False
                        latest_data["current_instruction"] = "Destination reached"
                        latest_data["distance_to_next_m"] = 0
                        speak_navigation("Destination reached")
                    else:
                        next_step = latest_data["steps"][latest_data["next_step_index"]]
                        latest_data["current_instruction"] = next_step["instruction"]

                last_nav_spoken_time = now
                time.sleep(2)
                continue

            if now - last_nav_spoken_time > NAV_REPEAT_COOLDOWN:
                speak_navigation(f"{step['instruction']}. Distance {int(dist)} meters")
                last_nav_spoken_time = now

            time.sleep(1)

        except Exception as e:
            print("Navigation loop error:", e)
            time.sleep(1)

# =========================================================
# SOS
# =========================================================
def trigger_sos():
    global danger_until

    msg, maps_link = build_sos_message()

    with danger_lock:
        latest_data["danger"] = True
        latest_data["danger_message"] = "PERSON IS IN DANGER"
        latest_data["maps_link"] = maps_link
        latest_data["sos_message"] = msg
        latest_data["browser_alert_sound"] = True
        danger_until = time.time() + SOS_ALERT_SECONDS

    speak_navigation("Emergency alert. Person is in danger.")
    tg_ok = send_telegram(msg)

    print("SOS sent")
    print("Telegram sent:", tg_ok)

def danger_reset_loop():
    global danger_until

    while True:
        try:
            now = time.time()

            with danger_lock:
                if latest_data["danger"] and now > danger_until:
                    latest_data["danger"] = False
                    latest_data["danger_message"] = "SAFE"
                    latest_data["browser_alert_sound"] = False

            time.sleep(0.5)

        except Exception as e:
            print("Danger loop error:", e)
            time.sleep(1)

# GPIO physical SOS button only
sos_button.when_pressed = trigger_sos

# =========================================================
# WEB UI
# =========================================================
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>ECHOFLUX SMART SAFETY SYSTEM</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {
      margin: 0;
      font-family: Arial, sans-serif;
      color: white;
      background: linear-gradient(135deg, #0f172a, #020617);
      overflow-x: hidden;
      transition: background 0.3s ease;
    }

    body.danger-mode {
      background: #7f1d1d;
    }

    .container {
      position: relative;
      z-index: 2;
      padding: 20px;
      max-width: 1250px;
      margin: auto;
    }

    .title {
      text-align: center;
      font-size: 36px;
      font-weight: bold;
      margin-bottom: 8px;
      letter-spacing: 2px;
    }

    .subtitle {
      text-align: center;
      font-size: 15px;
      color: #cbd5e1;
      margin-bottom: 20px;
    }

    .grid {
      display: grid;
      grid-template-columns: 390px 1fr;
      gap: 20px;
    }

    .card {
      background: rgba(30, 41, 59, 0.88);
      border-radius: 20px;
      padding: 22px;
      backdrop-filter: blur(10px);
      box-shadow: 0 10px 30px rgba(0,0,0,0.3);
      transition: all 0.3s ease;
    }

    .danger-card {
      background: rgba(127, 29, 29, 0.92);
      border: 2px solid rgba(254, 202, 202, 0.45);
    }

    .status {
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 24px;
      font-weight: bold;
      margin-bottom: 14px;
    }

    .dot {
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: #22c55e;
      box-shadow: 0 0 12px #22c55e;
    }

    .danger .dot {
      background: #ef4444;
      box-shadow: 0 0 18px #ef4444;
    }

    .alert {
      display: none;
      background: linear-gradient(135deg, #dc2626, #991b1b);
      padding: 15px;
      border-radius: 14px;
      text-align: center;
      font-size: 22px;
      font-weight: bold;
      margin-bottom: 16px;
      animation: pulse 1s infinite;
    }

    .alert.show {
      display: block;
    }

    @keyframes pulse {
      0% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.7; transform: scale(1.02); }
      100% { opacity: 1; transform: scale(1); }
    }

    .info-box {
      display: grid;
      gap: 12px;
    }

    .info-item {
      background: rgba(255,255,255,0.04);
      padding: 14px;
      border-radius: 14px;
    }

    .label {
      font-size: 13px;
      color: #93c5fd;
      margin-bottom: 4px;
    }

    .value {
      font-size: 17px;
      font-weight: 600;
      word-break: break-word;
    }

    .map-link {
      display: inline-block;
      margin-top: 16px;
      text-decoration: none;
      color: white;
      background: linear-gradient(135deg, #0ea5e9, #2563eb);
      padding: 12px 16px;
      border-radius: 12px;
      font-weight: bold;
    }

    .siren-box {
      margin-top: 16px;
      padding: 12px;
      border-radius: 12px;
      text-align: center;
      font-weight: bold;
      background: rgba(255,255,255,0.05);
    }

    .siren-on {
      background: #dc2626;
      color: white;
    }

    input, button {
      width: 100%;
      padding: 12px;
      border-radius: 12px;
      border: none;
      margin-top: 10px;
      font-size: 15px;
    }

    button {
      background: #2563eb;
      color: white;
      font-weight: bold;
      cursor: pointer;
      box-shadow: 0 8px 20px rgba(37,99,235,0.3);
    }

    #map {
      height: 680px;
      border-radius: 18px;
      overflow: hidden;
    }

    @media (max-width: 950px) {
      .grid {
        grid-template-columns: 1fr;
      }
      #map {
        height: 460px;
      }
    }
  </style>
</head>
<body>
<div class="container">
  <div class="title">ECHOFLUX SMART SYSTEM</div>
  <div class="subtitle">SAFE YOUR BEAUTIFUL LIFE</div>

  <div class="grid">
    <div class="card" id="statusCard">
      <div class="status" id="statusWrap">
        <div class="dot"></div>
        <div id="statusText">SAFE</div>
      </div>

      <div id="alertBox" class="alert">🚨 ALERT ALERT PERSON IS IN DANGER 🚨</div>

      <div class="info-box">
        <div class="info-item">
          <div class="label">Latitude</div>
          <div class="value" id="lat">Waiting...</div>
        </div>

        <div class="info-item">
          <div class="label">Longitude</div>
          <div class="value" id="lon">Waiting...</div>
        </div>

        <div class="info-item">
          <div class="label">Last Update</div>
          <div class="value" id="lastUpdate">Waiting...</div>
        </div>

        <div class="info-item">
          <div class="label">Danger Message</div>
          <div class="value" id="dangerMessage">SAFE</div>
        </div>

        <div class="info-item">
          <div class="label">Navigation Instruction</div>
          <div class="value" id="navInstruction">No route loaded</div>
        </div>

        <div class="info-item">
          <div class="label">Distance to Next Step</div>
          <div class="value" id="navDistance">--</div>
        </div>
      </div>

      <input id="destination" placeholder="Enter destination address">
      <button onclick="startRoute()">Start Navigation</button>
      <button onclick="stopRoute()">Stop Navigation</button>

      <a class="map-link" id="mapLink" href="#" target="_blank">Open in Google Maps</a>

      <div class="siren-box" id="sirenState">ALERT SOUND OFF</div>
    </div>

    <div class="card">
      <iframe id="map" src="" style="width:100%;height:680px;border:none;border-radius:18px;"></iframe>
    </div>
  </div>
</div>

<script>
let audioEnabled = false;
let audioContext = null;
let sirenInterval = null;
let currentDangerState = false;

function initAudio() {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
  audioEnabled = true;
}

document.body.addEventListener("click", () => {
  initAudio();
}, { once: true });

function playSirenBurst() {
  if (!audioEnabled || !audioContext) return;

  const osc = audioContext.createOscillator();
  const gain = audioContext.createGain();

  osc.type = "sawtooth";
  gain.gain.setValueAtTime(0.09, audioContext.currentTime);

  osc.connect(gain);
  gain.connect(audioContext.destination);

  const now = audioContext.currentTime;
  osc.frequency.setValueAtTime(650, now);
  osc.frequency.linearRampToValueAtTime(1200, now + 0.35);
  osc.frequency.linearRampToValueAtTime(650, now + 0.7);

  osc.start(now);
  osc.stop(now + 0.7);
}

function startSiren() {
  if (!audioEnabled || sirenInterval) return;
  playSirenBurst();
  sirenInterval = setInterval(playSirenBurst, 900);
}

function stopSiren() {
  if (sirenInterval) {
    clearInterval(sirenInterval);
    sirenInterval = null;
  }
}

async function fetchData() {
  try {
    const res = await fetch('/api');
    const data = await res.json();

    document.getElementById('lat').innerText = data.lat ?? 'Waiting...';
    document.getElementById('lon').innerText = data.lon ?? 'Waiting...';
    document.getElementById('lastUpdate').innerText = data.last_update ?? 'Waiting...';
    document.getElementById('dangerMessage').innerText = data.danger_message ?? 'SAFE';
    document.getElementById('navInstruction').innerText = data.current_instruction ?? 'No route loaded';
    document.getElementById('navDistance').innerText =
      data.distance_to_next_m !== null ? data.distance_to_next_m + ' m' : '--';

    const statusCard = document.getElementById('statusCard');
    const statusWrap = document.getElementById('statusWrap');
    const statusText = document.getElementById('statusText');
    const alertBox = document.getElementById('alertBox');
    const sirenState = document.getElementById('sirenState');
    const mapLink = document.getElementById('mapLink');
    const mapFrame = document.getElementById('map');

    if (data.maps_link) {
      mapLink.href = data.maps_link;
      mapFrame.src = data.maps_link + '&output=embed';
    } else if (data.lat && data.lon) {
      const googleUrl = `https://www.google.com/maps?q=${data.lat},${data.lon}`;
      mapLink.href = googleUrl;
      mapFrame.src = googleUrl + '&output=embed';
    }

    if (data.danger) {
      document.body.classList.add("danger-mode");
      statusCard.classList.add("danger-card");
      statusWrap.classList.add("danger");
      statusText.innerText = "DANGER";
      alertBox.classList.add("show");
      sirenState.innerText = "ALERT SOUND ACTIVE IN BROWSER";
      sirenState.classList.add("siren-on");

      if (!currentDangerState) {
        startSiren();
      }

    } else {
      document.body.classList.remove("danger-mode");
      statusCard.classList.remove("danger-card");
      statusWrap.classList.remove("danger");
      statusText.innerText = "SAFE";
      alertBox.classList.remove("show");
      sirenState.innerText = "ALERT SOUND OFF";
      sirenState.classList.remove("siren-on");

      if (currentDangerState) {
        stopSiren();
      }
    }

    currentDangerState = data.danger;

  } catch (e) {
    console.log("Fetch error:", e);
  }
}

async function startRoute() {
  const destination = document.getElementById("destination").value.trim();
  if (!destination) {
    alert("Enter destination");
    return;
  }

  const res = await fetch("/start_route", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ destination })
  });

  const data = await res.json();
  if (!data.ok) {
    alert(data.error || "Route error");
  }
}

async function stopRoute() {
  await fetch("/stop_route", { method: "POST" });
}

fetchData();
setInterval(fetchData, 1000);
</script>

</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(HTML_PAGE)

@app.route("/api")
def api():
    return jsonify(latest_data)

@app.route("/start_route", methods=["POST"])
def start_route():
    try:
        destination = request.json.get("destination", "").strip()
        if not destination:
            return jsonify({"ok": False, "error": "Destination required"}), 400

        with gps_lock:
            lat = latest_data["lat"]
            lon = latest_data["lon"]

        if lat is None or lon is None:
            return jsonify({"ok": False, "error": "GPS location not available"}), 400

        route_json = request_route(lat, lon, destination)
        steps = parse_navigation_steps(route_json)

        if not steps:
            return jsonify({"ok": False, "error": "No route steps received"}), 400

        with route_lock:
            latest_data["destination"] = destination
            latest_data["steps"] = steps
            latest_data["route_active"] = True
            latest_data["next_step_index"] = 0
            latest_data["current_instruction"] = steps[0]["instruction"]
            latest_data["distance_to_next_m"] = steps[0]["distance_m"]

        speak_navigation("Navigation started")
        speak_navigation(steps[0]["instruction"])

        return jsonify({
            "ok": True,
            "steps": len(steps),
            "first_instruction": steps[0]["instruction"]
        })

    except Exception as e:
        print("start_route error:", e)
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/stop_route", methods=["POST"])
def stop_route():
    with route_lock:
        latest_data["route_active"] = False
        latest_data["steps"] = []
        latest_data["next_step_index"] = 0
        latest_data["current_instruction"] = "Navigation stopped"
        latest_data["distance_to_next_m"] = None

    speak_navigation("Navigation stopped")
    return jsonify({"ok": True})

if __name__ == "__main__":
    threading.Thread(target=gps_loop, daemon=True).start()
    threading.Thread(target=navigation_loop, daemon=True).start()
    threading.Thread(target=danger_reset_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
