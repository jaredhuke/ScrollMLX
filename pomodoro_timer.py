import tkinter as tk
from tkinter import messagebox
import time


class PomodoroTimer:
    def __init__(self, root):
        self.root = root
        self.root.title('Pomodoro Timer')
        self.root.geometry('300x200')
        self.root.resizable(False, False)
        self.root.configure(bg='black')

        self.time_left = 25 * 60  # 25 minutes
        self.running = False

        self.label = tk.Label(root, text='25:00', font=('Helvetica', 48), bg='black', fg='orange')
        self.label.pack(pady=20)

        self.start_button = tk.Button(root, text='Start', command=self.start_timer, bg='black', fg='orange', bd=0, font=('Helvetica', 16))
        self.start_button.pack(pady=10)

        self.stop_button = tk.Button(root, text='Stop', command=self.stop_timer, bg='black', fg='orange', bd=0, font=('Helvetica', 16))
        self.stop_button.pack(pady=10)

    def start_timer(self):
        if not self.running:
            self.running = True
            self.countdown()

    def stop_timer(self):
        if self.running:
            self.running = False

    def countdown(self):
        if self.running:
            if self.time_left > 0:
                self.time_left -= 1
                mins, secs = divmod(self.time_left, 60)
                time_format = '{:02d}:{:02d}'.format(mins, secs)
                self.label.config(text=time_format)
                self.root.after(1000, self.countdown)
            else:
                messagebox.showinfo('Time Over', 'Pomodoro session completed!')
                self.time_left = 25 * 60
                self.label.config(text='25:00')
                self.running = False


if __name__ == '__main__':
    root = tk.Tk()
    timer = PomodoroTimer(root)
    root.mainloop()
