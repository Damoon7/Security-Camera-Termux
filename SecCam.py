#!/usr/bin/env python3
import os
from PIL import Image, ImageFilter
import numpy as np
from datetime import datetime
from argparse import ArgumentParser
from time import sleep, time
from subprocess import Popen, PIPE, DEVNULL, STDOUT, run
from statistics import stdev, mean, median


def get_normalized_diff(image1, image2):
    # Convert images to float arrays and normalize both brightness (mean) and contrast (std)
    arr1 = np.asarray(image1, dtype=np.float32)
    arr2 = np.asarray(image2, dtype=np.float32)
    arr1 = arr1 - np.mean(arr1)
    arr2 = arr2 - np.mean(arr2)

    std1 = float(np.std(arr1))
    std2 = float(np.std(arr2))
    if std1 > 1.0 and std2 > 1.0:
        arr2 = arr2 * (std1 / std2)

    return np.abs(arr1 - arr2)


def dif_of_images(image1, image2, noise_cutoff=10.0):
    diff = get_normalized_diff(image1, image2)
    # Ignore sensor noise below the calibrated cutoff and sum only significant pixel changes
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
        # Resize, convert to grayscale, and apply Gaussian blur to reduce point sensor noise
        processed = img.resize((436, 580)).convert('L').filter(ImageFilter.GaussianBlur(radius=2))
    os.remove(filename)
    return processed


def setup_num(camera_id, sensitivity=100, min_stable_frames=12):
    imgArray = []
    p995_history = []
    stable_count = 1
    total_taken = 0

    # Discard the first frame to avoid initial camera auto-exposure/focus jump on cold start
    print("Warming up camera...                ", end="\r")
    take_processed_photo(camera_id, 'img_set.jpeg')

    # Wait until the phone is placed down and the scene has no physical motion
    while stable_count < min_stable_frames:
        total_taken += 1
        print(f"Calibrating: {stable_count}/{min_stable_frames} stable (photo #{total_taken})   ", end="\r")
        img = take_processed_photo(camera_id, 'img_set.jpeg')
        if img is None:
            sleep(1)
            continue

        if imgArray:
            diff_mat = get_normalized_diff(imgArray[-1], img)
            p995 = float(np.percentile(diff_mat, 99.5))
            p995_history.append(p995)

            if len(p995_history) > min_stable_frames - 1:
                p995_history.pop(0)

            med_p995 = median(p995_history)

            # Reject only actual motion/placement spikes while keeping natural camera sensor variance
            stable_transitions = 0
            for val in reversed(p995_history):
                if val <= med_p995 * 2.0 + 6.0 and val < 25.0:
                    stable_transitions += 1
                else:
                    break
            stable_count = stable_transitions + 1

        imgArray.append(img)
        if len(imgArray) > min_stable_frames:
            imgArray.pop(0)

        if stable_count < min_stable_frames:
            sleep(1)

    # Step 1: Compute all pairwise differences across the stable calibration window
    pair_diffs = [
        get_normalized_diff(imgArray[x], imgArray[y])
        for x in range(len(imgArray))
        for y in range(x + 1, len(imgArray))
    ]

    # Step 2: Set pixel noise cutoff with a 35% margin above the 99.8th percentile of stationary pairs
    noise_998 = float(np.percentile(pair_diffs, 99.8))
    noise_995 = float(np.percentile(pair_diffs, 99.5))
    noise_cutoff = max(7.0, noise_998 * 1.35)

    # Step 3: Measure stationary fluctuations at both the probe level (99.5%) and final cutoff
    probe_delta = [
        dif_of_images(imgArray[x], imgArray[y], max(5.0, noise_995))
        for x in range(len(imgArray))
        for y in range(x + 1, len(imgArray))
    ]
    delta = [
        dif_of_images(imgArray[x], imgArray[y], noise_cutoff)
        for x in range(len(imgArray))
        for y in range(x + 1, len(imgArray))
    ]

    maxdel = max(delta)
    std = stdev(delta) if len(delta) > 1 else 0.0
    probe_max = max(probe_delta)
    probe_std = stdev(probe_delta) if len(probe_delta) > 1 else 0.0

    # Step 4: Combine statistical camera variance with minimum physical object area (~0.2% of frame)
    statistical_bound = int(maxdel * 2.5 + 6.0 * std + probe_max * 1.5 + 3.0 * probe_std)
    area_bound = int(500 * noise_cutoff)
    base_threshold = max(statistical_bound, area_bound)

    # Apply optional user sensitivity scaling (default 100% leaves base_threshold unchanged)
    sensitivity = max(1, sensitivity)
    threshold = max(1, int(base_threshold * (100.0 / sensitivity)))

    print(f"\nSet up in {total_taken} photos! (Cutoff: {noise_cutoff:.1f} | Thr: {threshold:,})")
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


def status_line(dif, delta):
    cl = datetime.now().strftime("%H:%M:%S")
    print(f"{cl} | Diff: {dif:,} / Thr: {int(delta):,}    ", end="\r")


# Clear the screen
run("clear", shell=True)

# Arguments:
parser = ArgumentParser()
parser.add_argument('-C', '--camera', help='0 for back Camera, 1 for front Camera', type=str, default='0')
parser.add_argument('-v', '-V', '--video', help='1 to convert photos to video file and 0 for dont convert', type=int, default=0)
parser.add_argument('-sms', '--sms', help='Enter phone number to send sms')
parser.add_argument('-c', '--call', help='Enter phone number to call')
parser.add_argument('-n', '--number', help='number of photos you want to be captured', type=int, default=50)
parser.add_argument('-st', '--sensitivity', help='Detection sensitivity percentage (default: 100)', type=int, default=100)
parser.add_argument('-ch', '--chatid', help='your chat id', type=str)
parser.add_argument('-bt', '--bottoken', help='your telegram bot token', type=str)
args = parser.parse_args()

# Wake up Termux while phone is locked:
run("termux-wake-lock", shell=True)

print('Setting up the device (place phone in position)...\n')

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
            print(f"Capturing photo {i+1}/{args.number}...   ", end="\r")
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
                    print(f"Uploading to telegram: {idx}/{len(photos)}   ", end="\r")
                else:
                    print("\nStopping upload due to connection/API error.")
                    break

            if args.video == 1 and os.path.exists(video_path):
                print("\nUploading video to telegram...")
                send_to_telegram(args.chatid, args.bottoken, video_path, "video")

            print(f"\n{sent_count}/{len(photos)} photos sent to your telegram bot.\n")

        print('\nAgain Setting up the device..')
        delta, noise_cutoff, last_img = setup_num(args.camera, args.sensitivity)
        if last_img is not None:
            imgArr = [last_img]
