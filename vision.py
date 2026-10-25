#!/usr/bin/env python3
import os
from time import sleep, time
from statistics import stdev, median
import numpy as np
from PIL import Image, ImageFilter, ImageOps
from config import TMP_PHOTO, PHOTO_TIMEOUT, run_cmd, remove_if_exists, log


def get_normalized_diff(image1, image2):
    if image2.size != image1.size:
        image2 = image2.resize(image1.size)
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
    excess = np.maximum(0.0, diff - noise_cutoff)
    return int(np.sum(excess))


def process_image_file(filepath):
    if not os.path.exists(filepath):
        return None
    try:
        with Image.open(filepath) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("L")
            img.thumbnail((320, 320))
            return img.filter(ImageFilter.GaussianBlur(radius=3))
    except Exception:
        return None


def take_processed_photo(camera_id, filename=TMP_PHOTO, keep_file=False):
    remove_if_exists(filename)
    run_cmd(["termux-camera-photo", "-c", camera_id, filename], timeout=PHOTO_TIMEOUT)
    processed = process_image_file(filename)
    if not keep_file or processed is None:
        remove_if_exists(filename)
    return processed


def check_camera(camera_id, attempts=2):
    for _ in range(attempts):
        if take_processed_photo(camera_id) is not None:
            return True
        sleep(2)
    return False


def prepare_tg_photo(filepath, max_dim=1920, quality=82):
    opt_path = filepath + ".tg.jpg"
    try:
        resample = getattr(Image, "Resampling", Image).LANCZOS
        with Image.open(filepath) as img:
            if img.format == "JPEG":
                img.draft("RGB", (max_dim, max_dim))
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_dim, max_dim), resample)
            img.convert("RGB").save(opt_path, "JPEG", quality=quality, optimize=True)
        return opt_path
    except Exception as exc:
        log.warning("Could not optimise %s for Telegram (%s); sending original", filepath, exc)
        remove_if_exists(opt_path)
        return filepath


def compute_threshold(img_array, sensitivity=100):
    pairs = [(x, y) for x in range(len(img_array)) for y in range(x + 1, len(img_array))]
    flat = np.concatenate([get_normalized_diff(img_array[x], img_array[y]).ravel()[::2]
                           for x, y in pairs])
    noise_998 = float(np.percentile(flat, 99.8))
    noise_995 = float(np.percentile(flat, 99.5))
    del flat
    noise_cutoff = max(7.0, noise_998 * 1.4)

    probe_delta = [dif_of_images(img_array[x], img_array[y], max(5.0, noise_995)) for x, y in pairs]
    delta = [dif_of_images(img_array[x], img_array[y], noise_cutoff) for x, y in pairs]

    maxdel = max(delta)
    std = stdev(delta) if len(delta) > 1 else 0.0
    probe_max = max(probe_delta)
    probe_std = stdev(probe_delta) if len(probe_delta) > 1 else 0.0

    statistical_bound = int(maxdel * 3.0 + 6.0 * std + probe_max * 1.5 + 3.0 * probe_std)
    area_bound = int(200 * noise_cutoff)
    base_threshold = max(statistical_bound, area_bound)

    sensitivity = max(1, sensitivity)
    threshold = max(1, int(base_threshold * (100.0 / sensitivity)))
    return threshold, noise_cutoff


def setup_num(camera_id, sensitivity=100, min_stable_frames=12, max_photos=200):
    img_array = []
    p995_history = []
    stable_count = 1
    total_taken = 0

    print("Warming up camera...                ", end="\r")
    take_processed_photo(camera_id, keep_file=False)

    while stable_count < min_stable_frames:
        if total_taken >= max_photos:
            if len(img_array) >= 3:
                log.warning("\nScene never fully stabilised; calibrating with current frames.")
                break
            raise RuntimeError("Camera is not producing usable photos.")

        total_taken += 1
        print(f"Calibrating: {stable_count}/{min_stable_frames} stable (photo #{total_taken})   ", end="\r")
        img = take_processed_photo(camera_id, keep_file=False)
        if img is None:
            sleep(1)
            continue

        if img_array:
            diff_mat = get_normalized_diff(img_array[-1], img)
            p995_history.append(float(np.percentile(diff_mat, 99.5)))
            if len(p995_history) > min_stable_frames - 1:
                p995_history.pop(0)

            med_p995 = median(p995_history)
            stable_transitions = 0
            for val in reversed(p995_history):
                if val <= med_p995 * 2.0 + 6.0 and val < 25.0:
                    stable_transitions += 1
                else:
                    break
            stable_count = stable_transitions + 1

        img_array.append(img)
        if len(img_array) > min_stable_frames:
            img_array.pop(0)

        if stable_count < min_stable_frames:
            sleep(1)

    threshold, noise_cutoff = compute_threshold(img_array, sensitivity)
    log.info("\nSet up in %d photos! (Cutoff: %.1f | Thr: %s)", total_taken, noise_cutoff, f"{threshold:,}")
    return threshold, noise_cutoff, img_array[-1]


class ThresholdAdapter:
    WINDOW = 12
    CALM_FRACTION = 0.85
    SMOOTHING = 0.5
    MIN_FACTOR, MAX_FACTOR = 1.0, 2.5

    def __init__(self, sensitivity, minutes, delta, cutoff):
        self.sensitivity = sensitivity
        self.interval = max(0.0, minutes) * 60.0
        self.enabled = self.interval > 0
        self.reset_base(delta, cutoff)

    def reset_base(self, delta, cutoff):
        self.base_delta = float(delta)
        self.base_cutoff = float(cutoff)
        self.frames = []
        self.last_adapt = time()

    def clear(self):
        self.frames = []

    def observe(self, img, dif, delta):
        if not self.enabled:
            return
        if dif <= delta * self.CALM_FRACTION:
            self.frames.append(img)
            if len(self.frames) > self.WINDOW:
                self.frames.pop(0)
        else:
            self.frames = []

    def maybe_adapt(self, delta, cutoff):
        if (not self.enabled or len(self.frames) < self.WINDOW
                or time() - self.last_adapt < self.interval):
            return delta, cutoff
        try:
            new_thr, new_cut = compute_threshold(self.frames, self.sensitivity)
        except Exception:
            log.exception("Threshold adaptation failed; keeping current values")
            self.frames = []
            self.last_adapt = time()
            return delta, cutoff

        def clamp(value, base):
            return min(max(value, base * self.MIN_FACTOR), base * self.MAX_FACTOR)

        a = self.SMOOTHING
        adapted_thr = max(1, int(a * clamp(new_thr, self.base_delta) + (1 - a) * delta))
        adapted_cut = a * clamp(new_cut, self.base_cutoff) + (1 - a) * cutoff
        if abs(adapted_thr - int(delta)) >= max(10, int(delta * 0.02)):
            log.info("\nThreshold adapted: %s -> %s (cutoff %.1f -> %.1f)",
                     f"{int(delta):,}", f"{adapted_thr:,}", cutoff, adapted_cut)
        else:
            adapted_thr = int(delta)
        self.frames = []
        self.last_adapt = time()
        return adapted_thr, adapted_cut
