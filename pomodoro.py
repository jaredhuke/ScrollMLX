import time
import os
from datetime import datetime, timedelta

WORK_DURATION = 25 * 60  # 25 minutes
BREAK_DURATION = 5 * 60  # 5 minutes

def play_sound():
    if os.name == 'nt':  # Windows
        os.system('start "" "C:\\Windows\\Media\\tada.wav"')
    elif os.name == 'posix':  # macOS and Linux
        os.system('afplay /System/Library/Sounds/Glass.aiff')

def countdown(duration):
    end_time = datetime.now() + timedelta(seconds=duration)
    while datetime.now() < end_time:
        remaining = end_time - datetime.now()
        print(f"Time remaining: {remaining}", end='\r')
        time.sleep(1)
    print("Time's up!                ")
    play_sound()

def pomodoro():
    print("Starting Pomodoro Timer...")
    while True:
        print("Work time!")
        countdown(WORK_DURATION)
        print("Break time!")
        countdown(BREAK_DURATION)
        user_input = input("Press Enter to start another cycle or 'q' to quit: ")
        if user_input.lower() == 'q':
            break

if __name__ == "__main__":
    pomodoro()
