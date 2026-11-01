from flask import Flask
from threading import Thread  # <-- Make sure this line is here!

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    # This pushes Flask into the background so your bot can start
    t = Thread(target=run)
    t.start()
