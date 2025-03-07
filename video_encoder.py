# Enhanced Video Encoding Script with AV1/HEVC Support
# Features: GPU acceleration detection, dynamic bitrate control, progress display with time remaining

import os
import subprocess
import sys
import math
import shutil
import re
import platform
from pathlib import Path

def get_validated_integer_input(prompt, default_value=None, min_value=None, max_value=None):
    """Validate integer input with range checks and default values"""
    while True:
        # Build dynamic prompt with parameters
        display_prompt = prompt
        if default_value is not None:
            display_prompt += f" (default: {default_value})"
        if min_value is not None:
            display_prompt += f" [min: {min_value}]"
        if max_value is not None:
            display_prompt += f" [max: {max_value}]"
        display_prompt += ": "

        try:
            user_input = input(display_prompt) or str(default_value)
            validated_input = int(user_input)
            
            # Range validation checks
            if min_value is not None and validated_input < min_value:
                print(f"Value must be ≥ {min_value}")
                continue
            if max_value is not None and validated_input > max_value:
                print(f"Value must be ≤ {max_value}")
                continue
            return validated_input
        except ValueError:
            print("Please enter a valid integer")

def find_ffmpeg_path():
    """Locate FFmpeg executable across different operating systems"""
    system = platform.system()
    
    # Try to find ffmpeg in PATH first (works on all platforms)
    try:
        if system == "Windows":
            # Windows-specific search using 'where'
            ffmpeg_path = subprocess.check_output(["where", "ffmpeg"], stderr=subprocess.DEVNULL).decode().strip()
        else:
            # Unix-like systems (Linux, macOS) use 'which'
            ffmpeg_path = subprocess.check_output(["which", "ffmpeg"], stderr=subprocess.DEVNULL).decode().strip()
        
        if ffmpeg_path:
            print(f"FFmpeg found at: {ffmpeg_path}")
            return ffmpeg_path
    except subprocess.CalledProcessError:
        pass

    # Platform-specific common installation paths
    common_paths = []
    
    if system == "Windows":
        # Windows common paths
        common_paths = [
            os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "ffmpeg", "bin", "ffmpeg.exe")
        ]
    elif system == "Darwin":  # macOS
        common_paths = [
            "/usr/local/bin/ffmpeg",
            "/opt/homebrew/bin/ffmpeg",
            "/opt/local/bin/ffmpeg"
        ]
    else:  # Linux and others
        common_paths = [
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/opt/ffmpeg/bin/ffmpeg"
        ]
    
    for path in common_paths:
        if os.path.isfile(path):
            print(f"FFmpeg found at: {path}")
            return path

    print("FFmpeg not found. Please install FFmpeg and ensure it's available in your PATH.")
    sys.exit(1)

def check_gpu_acceleration(ffmpeg_path):
    """Verify hardware acceleration support through codec list"""
    try:
        output = subprocess.check_output([ffmpeg_path, "-codecs"], stderr=subprocess.DEVNULL).decode()
        if "hwaccel" in output:
            print("GPU acceleration is supported.")
            return True
    except subprocess.CalledProcessError:
        pass

    print("GPU acceleration is not supported or could not be detected.")
    return False

def get_video_info(ffmpeg_path, input_file):
    """Extract video information using ffprobe for better efficiency"""
    try:
        # Adjust ffprobe path based on ffmpeg path
        system = platform.system()
        if system == "Windows":
            ffprobe_path = ffmpeg_path.replace("ffmpeg.exe", "ffprobe.exe")
        else:
            ffprobe_path = ffmpeg_path.replace("ffmpeg", "ffprobe")
        
        # Use ffprobe to get duration and FPS data
        probe_cmd = [
            ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate,duration",
            "-of", "csv=p=0",
            input_file
        ]
        
        probe_output = subprocess.check_output(probe_cmd, universal_newlines=True).strip().split(',')
        
        # Parse frame rate
        fps_fraction = probe_output[0]
        if '/' in fps_fraction:
            num, den = map(int, fps_fraction.split('/'))
            fps = num / den
        else:
            fps = float(fps_fraction)
        
        # Parse duration
        duration = float(probe_output[1]) if len(probe_output) > 1 else None
        
        # Calculate frame count from duration and fps
        frame_count = int(duration * fps) if duration and fps else None
        
        return {
            "frame_count": frame_count,
            "fps": fps,
            "duration": duration
        }
    except Exception as e:
        print(f"Error getting video info: {str(e)}")
        return {
            "frame_count": None,
            "fps": None,
            "duration": None
        }

def create_progress_bar(progress, width=20):
    """Create a simple text-based progress bar"""
    filled_width = int(width * progress / 100)
    bar = '█' * filled_width + '░' * (width - filled_width)
    return bar

def encode_video(video_name, input_path, output_path, encoding_command, crf, preset, keyframe_interval, gpu_acceleration, max_bitrate=None):
    """Core encoding function with progress monitoring and time remaining calculation"""
    input_file = os.path.join(input_path, video_name)
    output_file = os.path.join(output_path, f"{Path(video_name).stem} encoded av1.mkv")
    
    if not os.path.isfile(input_file):
        print(f"Input not found: {input_file}")
        return None, None

    # Get video information before encoding
    print("Analyzing video to calculate total frames...")
    video_info = get_video_info(ffmpeg_path, input_file)
    total_frames = video_info["frame_count"]
    video_fps = video_info["fps"]
    
    if total_frames:
        print(f"Total frames: {total_frames}, FPS: {video_fps:.2f}")
    else:
        print("Could not determine total frames, time remaining will not be shown")

    # Base FFmpeg command
    ffmpeg_cmd = [
        ffmpeg_path, "-y", "-i", input_file, "-map", "0",
        "-vf", "scale=1920:-1", 
        "-c:v", encoding_command,
        "-crf", str(crf),
        "-preset", str(preset),
        "-g", str(keyframe_interval),
        "-pix_fmt", "yuv420p10le",
        "-c:a", "copy"
    ]

    # Add GPU acceleration if available
    if gpu_acceleration:
        ffmpeg_cmd += ["-hwaccel", "auto"]

    # SVT-AV1 specific parameters
    if encoding_command == "libsvtav1":
        cores = max(4, min(8, math.floor(os.cpu_count() / 2)))
        svtav1_params = f"lp={cores}:mbr={max_bitrate}:enable-stat-report=1:tune=1:enable-overlays=1:enable-tf=1:scd=1"
        ffmpeg_cmd += ["-svtav1-params", svtav1_params]

    ffmpeg_cmd.append(output_file)

    try:
        # Convert all elements to strings to prevent type errors
        ffmpeg_cmd_str = [str(item) for item in ffmpeg_cmd]
        print(f"Starting FFmpeg process with command: {' '.join(ffmpeg_cmd_str)}")
        
        # Use subprocess.Popen with appropriate redirects for real-time output
        process = subprocess.Popen(
            ffmpeg_cmd_str, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            universal_newlines=True, 
            bufsize=1
        )
        
        # Real-time progress display
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            
            # Strip the line 
            line = line.strip()
            if line:
                # Enhanced progress display formatting
                if line.startswith("frame="):
                    # Extract the frame number
                    frame_match = re.search(r'frame=\s*(\d+)', line)
                    # Extract the encoding speed
                    speed_match = re.search(r'speed=\s*([\d.]+)x', line)
                    # Extract fps
                    fps_match = re.search(r'fps=\s*(\d+)', line)
                    # Extract current file size
                    size_match = re.search(r'size=\s*(\d+)KiB', line)
                    # Extract encoded time
                    time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                    # Extract bitrate
                    bitrate_match = re.search(r'bitrate=\s*([\d.]+)kbits/s', line)
                    
                    encoding_speed = 0
                    if speed_match:
                        encoding_speed = float(speed_match.group(1))
                        
                    # Parse additional information
                    current_fps = int(fps_match.group(1)) if fps_match else 0
                    current_size_kb = int(size_match.group(1)) if size_match else 0
                    current_size_mb = current_size_kb / 1024  # Convert to MB
                    
                    # Parse time (HH:MM:SS)
                    encoded_time = "00:00:00"
                    if time_match:
                        hours = time_match.group(1)
                        minutes = time_match.group(2)
                        seconds = time_match.group(3)
                        # Remove decimal from seconds
                        seconds_int = seconds.split('.')[0]
                        encoded_time = f"{hours}:{minutes}:{seconds_int}"
                        
                    # Parse bitrate
                    current_bitrate = 0
                    if bitrate_match:
                        current_bitrate = float(bitrate_match.group(1))
                    
                    if frame_match and total_frames and video_fps and encoding_speed > 0:
                        current_frame = int(frame_match.group(1))
                        remaining_frames = total_frames - current_frame
                        # Calculate time remaining (considering encoding speed)
                        seconds_remaining = int(remaining_frames / (video_fps * encoding_speed))
                        # Format time with leading zeros (HH:MM:SS)
                        hours_remaining = seconds_remaining // 3600
                        minutes_remaining = (seconds_remaining % 3600) // 60
                        seconds_remaining = seconds_remaining % 60
                        time_remaining = f"{hours_remaining:02d}:{minutes_remaining:02d}:{seconds_remaining:02d}"
                        # Calculate progress percentage
                        progress_percent = (current_frame / total_frames) * 100
                        # Create progress bar
                        progress_bar = create_progress_bar(progress_percent)
                        # Estimate new file size
                        if os.path.exists(output_file):
                            current_output_size = os.path.getsize(output_file)
                            estimated_final_size = current_output_size / (progress_percent / 100) if progress_percent > 0 else 0
                            estimated_final_size_mb = estimated_final_size / (1024 ** 2)  # Convert to MB
                        else:
                            estimated_final_size_mb = 0
                        # Format video duration properly
                        video_duration_formatted = format_duration(video_info["duration"])
                        # Format a fixed-width progress display with additional information
                        enhanced_progress = (
                            f"\rFrame: {current_frame:6d}/{total_frames:6d} | "
                            f"{progress_bar} {progress_percent:5.1f}% | "
                            f"{current_fps:3d}fps | "
                            f"{current_size_mb:5.1f}MB / est. {estimated_final_size_mb:6.1f}MB | "
                            f"Encoded: {encoded_time} / {video_duration_formatted} | "
                            f"est. {time_remaining} remaining | "
                            f"{current_bitrate:6.1f}kb/s | "
                            f"{encoding_speed:3.1f}x | "
                            f"Est. Size: {estimated_final_size_mb:6.1f}MB"
                        )
                        # Keep the line on the same line with carriage return
                        print(enhanced_progress, end='', flush=True)
                    else:
                        # If we couldn't calculate time remaining, just print the original line
                        print(f"\r{line}", end='', flush=True)
                else:
                    # Other output on new lines
                    print(line)
        
        # Get return code
        return_code = process.poll()
        if return_code != 0:
            print(f"\nFFmpeg process exited with error code {return_code}")
            return None, None
        
        # Final status report
        print("\nEncoding completed")
        return (output_file, calculate_size_reduction(input_file, output_file)) if os.path.exists(output_file) else (None, None)
    
    except Exception as e:
        print(f"Encoding failed: {str(e)}")
        return None, None

def calculate_size_reduction(original, encoded):
    """Calculate and format file size reduction statistics"""
    original_size = os.path.getsize(original)
    encoded_size = os.path.getsize(encoded)
    reduction = (original_size - encoded_size) / original_size * 100
    
    return f"""Original: {original_size/(1024**2):.1f}MB
Encoded: {encoded_size/(1024**2):.1f}MB
Reduction: {reduction:.1f}%"""

def format_duration(seconds):
    """Convert seconds (float) to HH:MM:SS format with leading zeros."""
    if not seconds:
        return "00:00:00"
    hours = int(seconds) // 3600
    minutes = (int(seconds) % 3600) // 60
    seconds = int(seconds) % 60
    return f"{hours:02}:{minutes:02}:{seconds:02}"

def get_recycle_bin_path():
    """Get platform-appropriate recycle bin or trash directory path"""
    system = platform.system()
    
    if system == "Windows":
        # Windows recycle bin is a special folder, use custom directory instead
        return os.path.join(Path.home(), "VideoEncoderRecycleBin")
    elif system == "Darwin":  # macOS
        return os.path.join(Path.home(), ".Trash")
    else:  # Linux
        return os.path.join(Path.home(), ".local", "share", "Trash", "files")

def encode_and_log(video_name, input_path, output_path, encoding_command, crf, preset, keyframe_interval, gpu_acceleration, max_bitrate, move_to_recycle_bin):
    """Handle encoding lifecycle with logging and file management"""
    output_file, log = encode_video(video_name, input_path, output_path, encoding_command, crf, preset, keyframe_interval, gpu_acceleration, max_bitrate)
    
    if output_file and log:
        log_file_path = os.path.join(output_path, f"{Path(output_file).stem}.log")
        with open(log_file_path, "w", encoding="utf-8") as log_file:
            log_file.write(log)
        
        if move_to_recycle_bin:
            try:
                # Get platform-appropriate recycle bin path
                recycle_bin_dir = get_recycle_bin_path()
                os.makedirs(recycle_bin_dir, exist_ok=True)
                
                original_file = os.path.join(input_path, video_name)
                destination_file = os.path.join(recycle_bin_dir, video_name)
                
                # If a file with the same name exists in the recycle bin, add a suffix
                if os.path.exists(destination_file):
                    base, ext = os.path.splitext(video_name)
                    destination_file = os.path.join(recycle_bin_dir, f"{base}_old{ext}")
                
                shutil.move(original_file, destination_file)
                print(f"Moved '{video_name}' to the recycle bin at: {recycle_bin_dir}")
            except Exception as e:
                print(f"Move failed: {e}")

# Main execution flow
if __name__ == "__main__":
    print("Cross-Platform Video Encoding Tool v2.2 - Supports HEVC/AV1 Conversion")
    print("--------------------------------------------------------")
    print("This tool will help you convert video files to efficient AV1 or HEVC format")
    print("with hardware acceleration when available.")
    print("--------------------------------------------------------")
    
    videos = []
    
    # Collect input files with improved prompts
    print("\nSTEP 1: Select video files to convert")
    print("Enter video filenames one by one. Make sure the files are in the current directory.")
    while True:
        video = input("Enter filename (with extension) or press Enter when done: ").strip()
        if not video:
            break
        
        # Verify file exists before adding
        if not os.path.isfile(video):
            print(f"Warning: '{video}' not found in the current directory. Please check the filename.")
            continue
            
        videos.append(video)
        print(f"Added: {video}")
    
    if not videos:
        sys.exit("No files provided. Exiting...")

    # Encoding configuration with improved prompts
    print("\nSTEP 2: Choose encoding settings")
    print("AV1 offers better compression but may be slower. HEVC is faster but with slightly larger files.")
    encoding = input("Choose encoding format (HEVC/AV1) [AV1]: ").upper() or "AV1"
    
    if encoding == "HEVC":
        codec = "libx265"
        keyframe = 1
        print("\nHEVC encoding selected. Configure quality settings:")
        print("Lower CRF = higher quality and larger file size (20-30 recommended for HD content)")
        crf = get_validated_integer_input("Quality (CRF value)", 30, 0, 51)
        
        print("\nPreset affects encoding speed and compression efficiency.")
        print("Options: ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow")
        print("Slower presets give better compression but take longer to encode.")
        preset = input("Preset (slower = better quality) [slow]: ") or "slow"
        max_bitrate = None
    elif encoding == "AV1":
        codec = "libsvtav1"
        keyframe = 150
        max_bitrate = 1500
        
        print("\nAV1 encoding selected. Configure quality settings:")
        print("Lower CRF = higher quality and larger file size (40-55 recommended for HD content)")
        crf = get_validated_integer_input("Quality (CRF value)", 50, 0, 63)
        
        print("\nPreset level affects encoding speed and compression efficiency.")
        print("Lower values = slower encoding but better quality & compression")
        print("Recommended values: 4-6 for best quality, 7-9 for good balance, 10+ for faster encoding")
        preset = get_validated_integer_input("Preset level", 6, 0, 13)
    else:
        sys.exit("Invalid encoding choice. Please restart and select either HEVC or AV1.")

    # System setup
    print("\nSTEP 3: System configuration")
    ffmpeg_path = find_ffmpeg_path()
    gpu_available = check_gpu_acceleration(ffmpeg_path)
    
    print("\nOriginal files can be moved to the recycle bin/trash after successful conversion.")
    recycle = input("Move originals to recycle bin after encoding? (Y/N) [N]: ").upper() == "Y"
    
    if recycle:
        recycle_path = get_recycle_bin_path()
        print(f"Original files will be moved to: {recycle_path}")

    # Process each video
    print("\nSTEP 4: Beginning conversion process")
    for i, video in enumerate(videos):
        print(f"\nProcessing file {i+1}/{len(videos)}: {video}")
        encode_and_log(video, os.getcwd(), os.getcwd(), codec, crf, preset, keyframe, gpu_available, max_bitrate, recycle)
    
    print("\nAll processing complete! Encoded files have been saved to the current directory.")