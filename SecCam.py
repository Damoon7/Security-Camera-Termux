#!/usr/bin/env python3
import os
from PIL import Image, ImageFilter
import numpy as np
from datetime import datetime
from argparse import ArgumentParser
from time import sleep, time
from subprocess import Popen, PIPE, DEVNULL, STDOUT, run
from statistics import stdev, mean


def get_normalized_diff(image1, image2):
    # Convert images to float arrays and normalize brightness to neutralize ambient light changes
    arr1 = np.asarray(image1, dtype=np.float32)
    arr2 = np.asarray(image2, dtype=np.float32)
    arr1 = arr1 - np.mean(arr1)
    arr2 = arr2 - np.mean(arr2)
    return np.abs(arr1 - arr2)


def dif_of_images(image1, image2, noise_cutoff=15.0):
    diff = get_normalized_diff(image1, image2)
    # Ignore sensor noise below the auto-calibrated cutoff and sum only significant pixel changes
    significant_changes = np.where(diff > noise_cutoff, diff, 0)
    return int(np.sum(significant_changes))


def processCommand(command, shellVal, stout, sterr, err):
    try:
        process = Popen(command, shell=shellVal, stdout=stout, stderr=sterr)
        process.communicate()
    except Exception:
        print(err)


def take_processed_photo(camera_id, filename):
    processCommand(f"termux-camera-photo -c {camera_id} {filename}", True, PIPE, PIPE, "Can't take photo")
    if not os.path.exists(filename):
        return None
    with Image.open(filename) as img:
        # Resize, convert to grayscale, and apply slight Gaussian blur to reduce point sensor noise
        processed = img.resize((436, 580)).convert('L').filter(ImageFilter.GaussianBlur(radius=2))
    os.remove(filename)
    return processed


def setup_num(camera_id, sensitivity=100, setup_photos=20):
    imgArray = []
    for i in range(setup_photos):
        print(f"Calibrating camera: photo {i+1}/{setup_photos}...", end="\r")
        img = take_processed_photo(camera_id, 'img_set.jpeg')
        if img is not None:
            imgArray.append(img)
        sleep(1)

    if len(imgArray) < 2:
        return 10000, 15.0, (imgArray[-1] if imgArray else None)

    # Step 1: Automatically measure pixel-level sensor noise from consecutive calibration frames
    consecutive_diffs = [get_normalized_diff(imgArray[i], imgArray[i + 1]) for i in range(len(imgArray) - 1)]
    noise_995 = float(np.percentile(consecutive_diffs, 99.5))
    noise_cutoff = max(6.0, noise_995 * 1.25)

    # Step 2: Measure frame-to-frame fluctuations across all pairs using the learned noise_cutoff
    delta = []
    for x in range(len(imgArray)):
        for y in range(x + 1, len(imgArray)):
            delta.append(dif_of_images(imgArray[x], imgArray[y], noise_cutoff))

    maxdel = max(delta)
    std = stdev(delta) if len(delta) > 1 else 0
    avg = mean(delta)

    # Step 3: Compute dynamic floor (~0.15% of frame pixels changing above noise_cutoff) and statistical ceiling
    dynamic_floor = int(350 * noise_cutoff)
    statistical_bound = int(maxdel + 3 * std + avg)
    base_threshold = max(statistical_bound, dynamic_floor)

    # Apply optional user sensitivity scaling (default 100% leaves base_threshold unchanged)
    sensitivity = max(1, sensitivity)
    threshold = int(base_threshold * (100.0 / sensitivity))

    print(f"\nDevice is set up! (Noise cutoff: {noise_cutoff:.1f} | Threshold: {threshold:,})")
    return threshold, noise_cutoff, imgArray[-1]


def send_to_telegram(Chat_ID, Bot_Token, filepath, media_type="photo"):
    endpoint = "sendPhoto" if media_type == "photo" else "sendVideo"
    cmd = f'curl -s --connect-timeout 15 -X POST -F chat_id="{Chat_ID}" -F {media_type}=@"{filepath}" https://api.telegram.org/bot{Bot_Token}/{endpoint}'
    process = Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE, text=True)
    stdout, stderr = process.communicate()
    if '"ok":true' not in stdout:
        print(f"\nTelegram Error: {stdout or stderr}")
        return False
    return True


def countdown(t):
    while t > 0:
        mins, secs = divmod(t, 60)
        print(f'{mins:02d}:{secs:02d}', end="\r")
        sleep(1)
        t -= 1


def status_line(dif, delta):
    cl = datetime.now().strftime("%H:%M:%S")
    print(f"{cl} | Diff: {dif:,} / Threshold: {int(delta):,}      ", end="\r")


# Clear the screen
run("clear", shell=True)

# Arguments:
parser = ArgumentParser()
parser.add_argument('-C', '--camera', help='0 for back Camera, 1 for front Camera', type=str, default='0')
parser.add_argument('-v', '-V', '--video', help='1 to convert photos to video file and 0 for dont convert', type=int, default=0)
parser.add_argument('-sms', '--sms', help='Enter phone number to send sms')
parser.add_argument('-c', '--call', help='Enter phone number to call')
parser.add_argument('-n', '--number', help='number of photos you want to be captured', type=int, default=50)
parser.add_argument('-s', '--seconds', help='Seconds needs to set the device', type=int, default=30)
parser.add_argument('-st', '--sensitivity', help='Detection sensitivity percentage (default: 100)', type=int, default=100)
parser.add_argument('-ch', '--chatid', help='your chat id', type=str)
parser.add_argument('-bt', '--bottoken', help='your telegram bot token', type=str)
args = parser.parse_args()

# Wake up Termux while phone is locked:
run("termux-wake-lock", shell=True)

countdown(args.seconds)
print('Setting up the device...\n')

delta, noise_cutoff, last_img = setup_num(args.camera, args.sensitivity)
if last_img is None:
    last_img = take_processed_photo(args.camera, 'photo.jpeg')

imgArr = [last_img]
sleep(1)

while True:
    new_img = take_processed_photo(args.camera, 'photo.jpeg')
    if new_img is None:
        sleep(1)
        continue

    imgArr.append(new_img)
    dif = dif_of_images(imgArr[0], imgArr[1], noise_cutoff)
    imgArr.pop(0)
    status_line(dif, delta)

    if dif > delta:
        print('\nAn External Object Detected')

        if args.sms is not None:
            processCommand(f'termux-sms-send -n "{args.sms}" "An External Object Detected"', True, PIPE, PIPE, "Can't send message to you")
            print(f"\nMessage sent to {args.sms}")

        if args.call is not None:
            print(f"\nCalling {args.call}\n")
            processCommand(f"termux-telephony-call {args.call}", True, DEVNULL, STDOUT, "Can't call you")

        event_folder = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        os.makedirs(event_folder, exist_ok=True)
        audio_path = os.path.join(event_folder, f"{event_folder}.aac")

        processCommand(f'termux-microphone-record -f "{audio_path}" -e aac', True, DEVNULL, STDOUT, "Can't record audio")
        print("\nIt's taking Photos...")

        start = time()
        for i in range(args.number):
            now = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            photo_path = os.path.join(event_folder, f"{now}_{i+1:03d}.jpeg")
            print(f"Capturing photo {i+1}/{args.number}...", end="\r")
            processCommand(f'termux-camera-photo -c {args.camera} "{photo_path}"', True, PIPE, PIPE, "Can't take photo")
        end = time()

        delTime = max(1.0, end - start)
        fr = max(1.0, round(args.number / delTime, 1))

        processCommand("termux-microphone-record -q", True, DEVNULL, STDOUT, "Can't stop audio recorder!")

        video_path = os.path.join(event_folder, f"{event_folder}.mp4")
        if args.video == 1:
            print('\nConverting photos to video...')
            ffmpeg_cmd = (
                f"ffmpeg -y -framerate {fr} -pattern_type glob -i '{event_folder}/*.jpeg' "
                f"-i '{audio_path}' -c:v libx264 -tune stillimage -c:a aac -b:a 192k "
                f"-pix_fmt yuv420p -shortest '{video_path}'"
            )
            processCommand(ffmpeg_cmd, True, PIPE, PIPE, "Can't convert to video")
            print(f"\nVideo file created at: {video_path}\n")

        if args.chatid is not None and args.bottoken is not None:
            msg_cmd = (
                f'curl -s --connect-timeout 10 -X POST -F chat_id="{args.chatid}" '
                f'-F text="An external object detected, here are the files:" '
                f'https://api.telegram.org/bot{args.bottoken}/sendMessage'
            )
            processCommand(msg_cmd, True, DEVNULL, STDOUT, "Can't send Text to telegram!")
            print('\nStart sending taken photos to telegram:\n')

            photos = [x for x in sorted(os.listdir(event_folder)) if x.endswith(".jpeg")]
            sent_count = 0
            for idx, x in enumerate(photos, 1):
                filepath = os.path.join(event_folder, x)
                if send_to_telegram(args.chatid, args.bottoken, filepath, "photo"):
                    sent_count += 1
                    print(f"Uploading photos to telegram: {idx} of {len(photos)}", end="\r")
                else:
                    print("\nStopping upload due to connection/API error.")
                    break

            if args.video == 1 and os.path.exists(video_path):
                print("\nUploading video to telegram...")
                send_to_telegram(args.chatid, args.bottoken, video_path, "video")

            print(f"\n{sent_count}/{len(photos)} photos sent to your telegram bot.\n")

        print('')
        countdown(3)
        print('\nAgain Setting up the device..')
        delta, noise_cutoff, last_img = setup_num(args.camera, args.sensitivity)
        if last_img is not None:
            imgArr = [last_img]
