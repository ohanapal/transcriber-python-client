import pyaudio
import wave
import threading
import time
import requests
from mss import mss
from PIL import Image, ImageDraw, ImageFont
import os
import signal
from datetime import datetime
import logging
import http.client as http_client
from requests.exceptions import RequestException
from screeninfo import get_monitors

# Configuration
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100
SERVER_URL = "https://transcribe.ohanapal.bot"
SCREENSHOT_INTERVAL = 10  # seconds

# Global variables
recording = True
audio_frames = []
session_folder = ""
audio_filename = None

# Enable HTTP logging
http_client.HTTPConnection.debuglevel = 0  # Disable HTTP connection debug logging

logging.basicConfig(level=logging.INFO)  # Set logging level to INFO
logging.getLogger().setLevel(logging.INFO)
requests_log = logging.getLogger("requests.packages.urllib3")
requests_log.setLevel(logging.INFO)
requests_log.propagate = True

def signal_handler(sig, frame):
    global recording
    print("Ctrl+C pressed. Stopping recording...")
    recording = False

signal.signal(signal.SIGINT, signal_handler)

def create_session_folder():
    global session_folder
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_folder = f"session_{timestamp}"
    os.makedirs(session_folder, exist_ok=True)
    print(f"Created session folder: {session_folder}")

def select_monitors():
    monitors = get_monitors()
    print("Available monitors:")
    for i, monitor in enumerate(monitors, start=1):
        print(f"{i}. {monitor.name}: {monitor.width}x{monitor.height} - "
              f"Position: ({monitor.x}, {monitor.y})")
    
    selected = input("Enter the numbers of the monitors you want to capture (comma-separated), or 'all': ")
    if selected.lower() == 'all':
        return list(range(len(monitors)))
    else:
        return [int(x) - 1 for x in selected.split(',')]

def get_max_speakers():
    return int(input("How many speakers? "))

def capture_and_save_screenshot(monitor_indices, session_id):
    global session_folder
    print(f"Capturing screenshot for monitors: {monitor_indices}")
    with mss() as sct:
        for idx in monitor_indices:
            print(f"Capturing monitor {idx + 1}")
            monitor = sct.monitors[idx + 1]
            screenshot = sct.grab(monitor)
            img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
            
            draw = ImageDraw.Draw(img)
            font = ImageFont.load_default()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            draw.text((10, 10), timestamp, font=font, fill=(255, 0, 0))
            
            filename = f"{session_folder}/screenshot_monitor_{idx + 1}_{timestamp.replace(':', '-')}.png"
            img.save(filename)
            print(f"Screenshot saved: {filename}")
            
            # Upload the screenshot immediately after saving
            try:
                image_response = upload_image_file(filename, session_id)
                print(f"Screenshot {filename} uploaded successfully.")
            except Exception as e:
                print(f"Failed to upload screenshot: {e}")

def list_audio_devices():
    p = pyaudio.PyAudio()
    info = p.get_host_api_info_by_index(0)
    numdevices = info.get('deviceCount')
    
    for i in range(numdevices):
        device_info = p.get_device_info_by_host_api_device_index(0, i)
        if device_info.get('maxInputChannels') > 0:  # Only input devices
            print(f"Input Device id {i} - {device_info.get('name')}")
    
    p.terminate()

def record_audio(session_id, max_speakers):
    global recording, audio_frames, session_folder, audio_filename
    p = pyaudio.PyAudio()
    
    # Automatically use the default input device
    device_index = None  # None will use the default device

    try:
        stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, 
                        frames_per_buffer=CHUNK, input_device_index=device_index)
    except IOError as e:
        print(f"Error opening stream: {e}")
        p.terminate()
        return

    print("* Recording audio")
    start_time = time.time()
    frames_count = 0

    while recording:
        try:
            data = stream.read(CHUNK, exception_on_overflow=False)
            audio_frames.append(data)
            frames_count += 1
            if frames_count % 100 == 0:  # Print every ~1 second
                duration = time.time() - start_time
                print(f"Recording... Duration: {duration:.2f} seconds, Frames: {frames_count}, Data size: {len(b''.join(audio_frames))} bytes")
        except IOError as e:
            print(f"Error recording audio: {e}")

    stream.stop_stream()
    stream.close()
    p.terminate()
    print("Audio recording stopped")

    # Save the audio file
    audio_filename = f"{session_folder}/audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
    wf = wave.open(audio_filename, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(p.get_sample_size(FORMAT))
    wf.setframerate(RATE)
    wf.writeframes(b''.join(audio_frames))
    wf.close()
    print(f"Audio saved: {audio_filename}")

    # Upload the audio file immediately after saving
    try:
        audio_response = upload_audio_file(audio_filename, session_id, max_speakers)
        print(f"Audio file {audio_filename} uploaded successfully.")
    except Exception as e:
        print(f"Failed to upload audio: {e}")

def screenshot_thread(monitor_indices, session_id):
    global recording
    print(f"Screenshot thread started for monitors: {monitor_indices}")
    screenshot_count = 0
    while recording:
        capture_and_save_screenshot(monitor_indices, session_id)
        screenshot_count += 1
        print(f"Screenshot {screenshot_count} captured and uploaded. Sleeping for {SCREENSHOT_INTERVAL} seconds before next capture...")
        time.sleep(SCREENSHOT_INTERVAL)
    print(f"Screenshot thread stopped. Total screenshots: {screenshot_count}")

def upload_audio_file(audio_file_path, session_id, max_speakers):
    url = f"{SERVER_URL}/upload/audio"
    retries = 3
    backoff_factor = 2

    for attempt in range(retries):
        try:
            with open(audio_file_path, 'rb') as file:
                files = {'file': (os.path.basename(audio_file_path), file, 'audio/wav')}
                data = {'session_id': session_id, 'max_speakers': max_speakers}
                print(f"Uploading audio file {audio_file_path} to {url}")
                response = requests.post(url, files=files, data=data, timeout=60)  # Increased timeout
                response.raise_for_status()
                print(f"Audio file uploaded successfully. Server response: {response.status_code}")
                return response.json()
        except requests.RequestException as e:
            print(f"Failed to upload audio file: {e}")
            if attempt < retries - 1:
                sleep_time = backoff_factor ** attempt
                print(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
            else:
                raise

def upload_image_file(image_file_path, session_id):
    url = f"{SERVER_URL}/upload/image"
    retries = 3
    backoff_factor = 2

    for attempt in range(retries):
        try:
            with open(image_file_path, 'rb') as file:
                files = {'file': (os.path.basename(image_file_path), file, 'image/png')}
                data = {'session_id': session_id}
                print(f"Uploading image file {image_file_path} to {url}")
                response = requests.post(url, files=files, data=data, timeout=60)  # Increased timeout
                response.raise_for_status()
                print(f"Image file uploaded successfully. Server response: {response.status_code}")
                return response.json()
        except requests.RequestException as e:
            print(f"Failed to upload image file: {e}")
            if attempt < retries - 1:
                sleep_time = backoff_factor ** attempt
                print(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
            else:
                raise

def send_data_to_server(session_folder, session_id, max_speakers):
    print(f"Sending data from {session_folder} to server...")
    for filename in os.listdir(session_folder):
        filepath = os.path.join(session_folder, filename)
        if filename.startswith("audio_"):
            print(f"Skipping audio file: {filename}")  # Skip uploading audio files again
        elif filename.startswith("screenshot_"):
            print(f"Uploading image file: {filename}")
            image_response = upload_image_file(filepath, session_id)
            print(f"Image file {filename} uploaded successfully.")
        else:
            print(f"Skipping file: {filename}")

def main():
    global recording, session_folder, audio_filename
    
    try:
        print("Starting main function...")
        create_session_folder()
        
        selected_monitors = select_monitors()
        max_speakers = get_max_speakers()
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        print(f"Selected monitors: {[i+1 for i in selected_monitors]}")
        print(f"Max speakers set to: {max_speakers}")
        
        print("Starting audio recording thread...")
        audio_thread = threading.Thread(target=record_audio, args=(session_id, max_speakers))
        audio_thread.start()
        
        print("Starting screenshot thread...")
        ss_thread = threading.Thread(target=screenshot_thread, args=(selected_monitors, session_id))
        ss_thread.start()
        
        print("Recording and screen capture started. Press Ctrl+C to stop...")
        
        try:
            while recording:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        
        print("Waiting for threads to finish...")
        audio_thread.join()
        ss_thread.join()
        
        if audio_filename:
            upload_audio_file(audio_filename, session_id, max_speakers)
        else:
            print("No audio file was created.")
        
        print("Session completed.")
    
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        print("Recording and screenshot capture completed.")

if __name__ == "__main__":
    print("Script started")
    main()
    print("Script ended")