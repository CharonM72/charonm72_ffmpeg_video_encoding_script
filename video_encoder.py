# Enhanced Video Encoding Script with AV1/HEVC Support
# Features: GPU acceleration detection, dynamic bitrate control, progress display with time remaining,
#           optional combining of multiple videos into a single encoded file

import os
import subprocess
import sys
import math
import shutil
import re
import json
import platform
import tempfile
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
    """Check whether FFmpeg reports any hardware decoding methods"""
    try:
        output = subprocess.check_output([ffmpeg_path, "-hide_banner", "-hwaccels"], stderr=subprocess.DEVNULL).decode()
        # First line is a header ("Hardware acceleration methods:"), the rest are method names
        methods = [line.strip() for line in output.splitlines()[1:] if line.strip()]
        if methods:
            print(f"GPU acceleration is supported ({', '.join(methods)}).")
            return True
    except (subprocess.CalledProcessError, OSError):
        pass

    print("GPU acceleration is not supported or could not be detected.")
    return False

def get_ffprobe_path(ffmpeg_path):
    """Derive the ffprobe path from the ffmpeg path (only the filename is changed, never the folders)"""
    directory, filename = os.path.split(ffmpeg_path)
    return os.path.join(directory, filename.replace("ffmpeg", "ffprobe", 1))

def get_video_info(ffmpeg_path, input_file):
    """Extract video information using ffprobe for better efficiency"""
    try:
        # Adjust ffprobe path based on ffmpeg path
        ffprobe_path = get_ffprobe_path(ffmpeg_path)
        
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
        
        # Parse duration - handle 'N/A' case
        duration = None
        if len(probe_output) > 1 and probe_output[1] != 'N/A':
            duration = float(probe_output[1])
        
        # If duration is None, try an alternative approach for MKV files
        if duration is None:
            # Try to get duration from format information instead
            alt_probe_cmd = [
                ffprobe_path,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                input_file
            ]
            
            try:
                alt_duration = subprocess.check_output(alt_probe_cmd, universal_newlines=True).strip()
                if alt_duration and alt_duration != 'N/A':
                    duration = float(alt_duration)
            except:
                # If that also fails, we'll continue with duration as None
                pass
        
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

def get_stream_signature(ffprobe_path, input_file):
    """Return (video_signature, audio_signature) lists describing a file's streams"""
    probe_cmd = [
        ffprobe_path,
        "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
        "-of", "json",
        input_file
    ]
    data = json.loads(subprocess.check_output(probe_cmd, universal_newlines=True))
    
    video_sig, audio_sig = [], []
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            video_sig.append((stream.get("codec_name"), stream.get("width"), stream.get("height"), stream.get("r_frame_rate")))
        elif stream.get("codec_type") == "audio":
            audio_sig.append((stream.get("codec_name"), stream.get("sample_rate"), stream.get("channels")))
    return video_sig, audio_sig

def check_combine_compatibility(ffmpeg_path, video_names):
    """Compare every file's stream layout against the first file.
    Returns (video_issues, audio_issues), or None if the files could not be probed."""
    ffprobe_path = get_ffprobe_path(ffmpeg_path)
    try:
        signatures = [get_stream_signature(ffprobe_path, name) for name in video_names]
    except Exception as e:
        print(f"Could not verify file compatibility: {str(e)}")
        return None
    
    base_video, base_audio = signatures[0]
    video_issues, audio_issues = [], []
    for name, (video_sig, audio_sig) in zip(video_names[1:], signatures[1:]):
        if video_sig != base_video:
            video_issues.append(f"'{name}' has different video settings (codec/resolution/frame rate) than '{video_names[0]}'")
        if audio_sig != base_audio:
            audio_issues.append(f"'{name}' has different audio settings (codec/sample rate/channels) than '{video_names[0]}'")
    return video_issues, audio_issues

def create_progress_bar(progress, width=20):
    """Create a simple text-based progress bar"""
    filled_width = int(width * progress / 100)
    bar = '█' * filled_width + '░' * (width - filled_width)
    return bar

MIN_PROGRESS_WIDTH = 90  # Columns needed to show the full two-line progress display

class LiveDisplay:
    """Redraws a block of 1-2 lines in place using ANSI cursor movement"""
    def __init__(self):
        self.lines_drawn = 0
        if platform.system() == "Windows":
            os.system("")  # Enables ANSI escape code support in the Windows console

    def update(self, lines):
        # Re-read the width every tick so widening the window takes effect immediately
        width = max(20, shutil.get_terminal_size((120, 20)).columns - 1)
        clipped = [line[:width] for line in lines]
        
        out = "\r"
        if self.lines_drawn > 1:
            out = f"\033[{self.lines_drawn - 1}A\r"  # Move back up to the first line of the block
        out += "\n".join(f"{line}\033[K" for line in clipped)  # \033[K clears leftovers on each line
        
        # If the block shrank (2 lines -> 1), clear the line that's no longer used
        extra = self.lines_drawn - len(clipped)
        if extra > 0:
            out += ("\n\033[K" * extra) + f"\033[{extra}A"
        
        sys.stdout.write(out)
        sys.stdout.flush()
        self.lines_drawn = len(clipped)

    def finish(self):
        """End the current block so normal printing starts on a fresh line"""
        if self.lines_drawn:
            sys.stdout.write("\n")
            sys.stdout.flush()
        self.lines_drawn = 0

def build_ffmpeg_command(input_args, output_file, encoding_command, crf, preset, keyframe_interval, gpu_acceleration, max_bitrate, audio_args):
    """Build the FFmpeg command shared by single-file and combined encodes"""
    ffmpeg_cmd = [ffmpeg_path, "-y"]

    # -hwaccel is an input option, so it must come before the input arguments
    if gpu_acceleration:
        ffmpeg_cmd += ["-hwaccel", "auto"]

    ffmpeg_cmd += [
        *input_args, "-map", "0",
        "-vf", "scale=1920:-1", 
        "-c:v", encoding_command,
        "-crf", str(crf),
        "-preset", str(preset),
        "-g", str(keyframe_interval),
        "-pix_fmt", "yuv420p10le",
        *audio_args
    ]

    # SVT-AV1 specific parameters
    if encoding_command == "libsvtav1":
        cores = max(4, min(8, math.floor(os.cpu_count() / 2)))
        svtav1_params = f"lp={cores}:mbr={max_bitrate}:enable-stat-report=1:tune=1:enable-overlays=1:enable-tf=1:scd=1"
        ffmpeg_cmd += ["-svtav1-params", svtav1_params]

    ffmpeg_cmd.append(output_file)
    return ffmpeg_cmd

def run_ffmpeg_with_progress(ffmpeg_cmd, output_file, total_frames, video_fps, total_duration):
    """Run FFmpeg with real-time progress display. Returns True on success."""
    display = LiveDisplay()
    try:
        # Convert all elements to strings to prevent type errors
        ffmpeg_cmd_str = [str(item) for item in ffmpeg_cmd]
        print(f"Starting FFmpeg process with command: {' '.join(ffmpeg_cmd_str)}")
        
        # Warn right above the progress display if the window is too narrow to show it all
        terminal_width = shutil.get_terminal_size((120, 20)).columns
        if terminal_width < MIN_PROGRESS_WIDTH:
            print(f"Note: this window is {terminal_width} columns wide, but the progress display needs about "
                  f"{MIN_PROGRESS_WIDTH}. Widen the window to see everything (until then, lines are cut off at the edge).")
        
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
                        video_duration_formatted = format_duration(total_duration)
                        
                        # Two fixed-width lines, each about 85 characters
                        line_one = (
                            f"Frame: {current_frame:6d}/{total_frames:6d} | "
                            f"{progress_bar} {progress_percent:5.1f}% | "
                            f"Encoded: {encoded_time} / {video_duration_formatted}"
                        )
                        line_two = (
                            f"{current_fps:3d}fps | "
                            f"{encoding_speed:3.1f}x | "
                            f"Bitrate: {current_bitrate:6.1f}kb/s | "
                            f"{current_size_mb:5.1f}MB / est. {estimated_final_size_mb:6.1f}MB | "
                            f"est. {time_remaining} remaining"
                        )
                        display.update([line_one, line_two])
                    else:
                        # If we couldn't calculate time remaining, just show the original line
                        display.update([line])
                else:
                    # Other output goes on its own lines, below any progress block
                    display.finish()
                    print(line)
        
        # Get return code
        return_code = process.poll()
        if return_code != 0:
            print(f"\nFFmpeg process exited with error code {return_code}")
            return False
        
        # Final status report
        print("\nEncoding completed")
        return True
    
    except Exception as e:
        display.finish()
        print(f"Encoding failed: {str(e)}")
        return False

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

    ffmpeg_cmd = build_ffmpeg_command(
        ["-i", input_file], output_file, encoding_command, crf, preset,
        keyframe_interval, gpu_acceleration, max_bitrate, ["-c:a", "copy"]
    )

    if not run_ffmpeg_with_progress(ffmpeg_cmd, output_file, total_frames, video_fps, video_info["duration"]):
        return None, None
    
    return (output_file, calculate_size_reduction(input_file, output_file)) if os.path.exists(output_file) else (None, None)

def write_concat_list(input_files):
    """Write an FFmpeg concat-demuxer list file (in the given order) and return its path"""
    list_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    try:
        for file_path in input_files:
            # Forward slashes work on every platform; single quotes must be escaped for the list syntax
            escaped = Path(os.path.abspath(file_path)).as_posix().replace("'", "'\\''")
            list_file.write(f"file '{escaped}'\n")
    finally:
        list_file.close()
    return list_file.name

def encode_combined(video_names, input_path, output_path, output_name, encoding_command, crf, preset, keyframe_interval, gpu_acceleration, max_bitrate, audio_args):
    """Append all videos in order and encode them as one output file in a single pass"""
    input_files = [os.path.join(input_path, name) for name in video_names]
    output_file = os.path.join(output_path, output_name)
    
    for file_path in input_files:
        if not os.path.isfile(file_path):
            print(f"Input not found: {file_path}")
            return None, None

    # Sum frames and duration across every file so progress/ETA cover the whole combined video
    print("Analyzing videos to calculate combined total frames...")
    total_frames = 0
    total_duration = 0.0
    video_fps = None
    info_complete = True
    for file_path in input_files:
        info = get_video_info(ffmpeg_path, file_path)
        if video_fps is None:
            video_fps = info["fps"]
        if info["frame_count"] and info["duration"]:
            total_frames += info["frame_count"]
            total_duration += info["duration"]
        else:
            info_complete = False
    
    if not info_complete:
        total_frames = None
        total_duration = None
    
    if total_frames:
        print(f"Combined total frames: {total_frames}, FPS: {video_fps:.2f}")
    else:
        print("Could not determine total frames, time remaining will not be shown")

    list_path = write_concat_list(input_files)
    try:
        ffmpeg_cmd = build_ffmpeg_command(
            ["-f", "concat", "-safe", "0", "-i", list_path], output_file, encoding_command, crf, preset,
            keyframe_interval, gpu_acceleration, max_bitrate, audio_args
        )
        success = run_ffmpeg_with_progress(ffmpeg_cmd, output_file, total_frames, video_fps, total_duration)
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass
    
    if not success or not os.path.exists(output_file):
        return None, None

    source_list = "\n".join(f"  {i}. {name}" for i, name in enumerate(video_names, 1))
    log = f"Combined from (in order):\n{source_list}\n\n{calculate_size_reduction(input_files, output_file)}"
    return output_file, log

def calculate_size_reduction(original, encoded):
    """Calculate and format file size reduction statistics (original may be one path or a list)"""
    originals = original if isinstance(original, (list, tuple)) else [original]
    original_size = sum(os.path.getsize(path) for path in originals)
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

def move_original_to_recycle_bin(video_name, input_path):
    """Move an original video into the platform-appropriate recycle bin directory"""
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

def encode_and_log(video_name, input_path, output_path, encoding_command, crf, preset, keyframe_interval, gpu_acceleration, max_bitrate, move_to_recycle_bin):
    """Handle encoding lifecycle with logging and file management"""
    output_file, log = encode_video(video_name, input_path, output_path, encoding_command, crf, preset, keyframe_interval, gpu_acceleration, max_bitrate)
    
    if output_file and log:
        log_file_path = os.path.join(output_path, f"{Path(output_file).stem}.log")
        with open(log_file_path, "w", encoding="utf-8") as log_file:
            log_file.write(log)
        
        if move_to_recycle_bin:
            move_original_to_recycle_bin(video_name, input_path)

def encode_and_log_combined(video_names, input_path, output_path, output_name, encoding_command, crf, preset, keyframe_interval, gpu_acceleration, max_bitrate, audio_args, move_to_recycle_bin):
    """Combined-file equivalent of encode_and_log. Originals are only moved if the combined encode succeeds."""
    output_file, log = encode_combined(video_names, input_path, output_path, output_name, encoding_command, crf, preset, keyframe_interval, gpu_acceleration, max_bitrate, audio_args)
    
    if output_file and log:
        log_file_path = os.path.join(output_path, f"{Path(output_file).stem}.log")
        with open(log_file_path, "w", encoding="utf-8") as log_file:
            log_file.write(log)
        
        if move_to_recycle_bin:
            for video_name in video_names:
                move_original_to_recycle_bin(video_name, input_path)

def build_audio_args(audio_mode, opus_bitrate):
    """Return the FFmpeg audio arguments for the chosen audio handling"""
    if audio_mode == "opus":
        return ["-c:a", "libopus", "-b:a", f"{opus_bitrate}k"]
    return ["-c:a", "copy"]

# Main execution flow
if __name__ == "__main__":
    print("Cross-Platform Video Encoding Tool v2.3 - Supports HEVC/AV1 Conversion")
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

    # Optional: combine all videos into one file
    combine = False
    combined_name = None
    audio_mode = "copy"
    opus_bitrate = 128
    
    print("\nSTEP 3: Combine videos (optional)")
    if len(videos) < 2:
        print("Skipped - only one video was provided.")
    else:
        print("All listed videos can be joined into ONE encoded file, in this order:")
        for i, name in enumerate(videos, 1):
            print(f"  {i}. {name}")
        print("This works best for continuous recordings that were auto-split (e.g., by OBS),")
        print("where the end of one file picks up right where the next one begins.")
        combine = input("Combine all videos into a single file? (Y/N) [N]: ").strip().upper() == "Y"
        
        if combine:
            default_name = f"{Path(videos[0]).stem} combined {encoding.lower()}.mkv"
            print("\nThe combined file will be saved in the current directory.")
            combined_name = input(f"Output filename [{default_name}]: ").strip() or default_name
            if not Path(combined_name).suffix:
                combined_name += ".mkv"
            
            print("\nAudio handling for the combined file:")
            print("  copy - keep the original audio untouched (fastest, no quality loss).")
            print("         Best for auto-split files, which share identical audio settings.")
            print("  opus - re-encode audio to Opus (use if the files have different audio settings,")
            print("         or if you want smaller audio).")
            audio_mode = input("Audio handling (copy/opus) [copy]: ").strip().lower() or "copy"
            if audio_mode not in ("copy", "opus"):
                print("Unrecognized choice, using copy.")
                audio_mode = "copy"
            if audio_mode == "opus":
                opus_bitrate = get_validated_integer_input("Opus bitrate per audio track (kb/s)", 128, 32, 512)

    # System setup
    print("\nSTEP 4: System configuration")
    ffmpeg_path = find_ffmpeg_path()
    gpu_available = check_gpu_acceleration(ffmpeg_path)
    
    print("\nOriginal files can be moved to the recycle bin/trash after successful conversion.")
    recycle = input("Move originals to recycle bin after encoding? (Y/N) [N]: ").upper() == "Y"
    
    if recycle:
        recycle_path = get_recycle_bin_path()
        print(f"Original files will be moved to: {recycle_path}")

    # Verify the files can be safely joined before starting a long encode
    if combine:
        print("\nChecking that the files are compatible for combining...")
        issues = check_combine_compatibility(ffmpeg_path, videos)
        if issues is None:
            print("Continuing with your chosen settings.")
        else:
            video_issues, audio_issues = issues
            
            if audio_issues and audio_mode == "copy":
                for issue in audio_issues:
                    print(f"Warning: {issue}")
                print("Audio can't be copied reliably when settings differ, so audio will be re-encoded to Opus instead.")
                audio_mode = "opus"
                opus_bitrate = get_validated_integer_input("Opus bitrate per audio track (kb/s)", 128, 32, 512)
            
            if video_issues:
                for issue in video_issues:
                    print(f"Warning: {issue}")
                print("Joining files with different video settings may cause glitches at the join points.")
                if input("Combine anyway? (Y/N) [N]: ").strip().upper() != "Y":
                    print("Combining cancelled. The files will be encoded separately instead.")
                    combine = False
            
            if combine and not video_issues and not audio_issues:
                print("All files are compatible.")

    # Process videos
    print("\nSTEP 5: Beginning conversion process")
    if combine:
        audio_args = build_audio_args(audio_mode, opus_bitrate)
        print(f"\nCombining {len(videos)} files into: {combined_name}")
        encode_and_log_combined(videos, os.getcwd(), os.getcwd(), combined_name, codec, crf, preset, keyframe, gpu_available, max_bitrate, audio_args, recycle)
    else:
        for i, video in enumerate(videos):
            print(f"\nProcessing file {i+1}/{len(videos)}: {video}")
            encode_and_log(video, os.getcwd(), os.getcwd(), codec, crf, preset, keyframe, gpu_available, max_bitrate, recycle)
    
    print("\nAll processing complete! Encoded files have been saved to the current directory.")