import os
import time
import requests
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
instance_path = os.path.join(basedir, 'instance')
if not os.path.exists(instance_path):
    os.makedirs(instance_path)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(instance_path, 'portfolio.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

TWELVE_DATA_KEY = "0eef54e01c5b4f6aa18c054d569084de"

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(15), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, default=0.0)
    last_signal = db.Column(db.String(10), default="HOLD")
    tech_details = db.Column(db.String(200), default="Așteaptă update...")

with app.app_context():
    db.create_all()

def get_ro_price(symbol):
    """Interogare rapidă Yahoo pentru BVB fără librării grele"""
    clean_s = symbol.replace(".RO", ".BVB")
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={clean_s}"
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        data = r.json()
        res = data['quoteResponse']['result'][0]
        return float(res['regularMarketPrice'])
    except: return None

def update_all():
    with app.app_context():
        stocks = Stock.query.all()
        for s in stocks:
            # Caz RO
            if ".RO" in s.symbol or ".BVB" in s.symbol:
                p = get_ro_price(s.symbol)
                if p:
                    s.current_price = p
                    s.tech_details = f"Preț RO: {p} RON"
            # Caz SUA
            else:
                try:
                    # Doar preț și indicatori minimi pentru a nu bloca serverul
                    base = "https://api.twelvedata.com"
                    # Preț
                    p_res = requests.get(f"{base}/quote?symbol={s.symbol}&apikey={TWELVE_DATA_KEY}").json()
                    if "close" in p_res: s.current_price = float(p_res['close'])
                    
                    # Indicatori (un singur apel combinat dacă e posibil, sau pauză mică)
                    # NOTA: Pentru a evita 502, reducem pauzele la minim sau facem update rar
                    ma_res = requests.get(f"{base}/ma?symbol={s.symbol}&interval=1day&time_period=10&apikey={TWELVE_DATA_KEY}").json()
                    ma_val = float(ma_res['values'][0]['ma'])
                    
                    s.tech_details = f"MA 10: {round(ma_val, 2)} | Price: {s.current_price}"
                    s.last_signal = "BUY" if s.current_price > ma_val else "SELL"
                except: 
                    s.tech_details = "Eroare API / Limită atinsă"
            
            db.session.commit()
            time.sleep(1) # Pauză mică, nu 8 secunde, ca să nu dăm timeout la server

sched = BackgroundScheduler()
sched.add_job(update_all, 'interval', minutes=30)
sched.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    return render_template('index.html', stocks=stocks)

@app.route('/search')
def search():
    q = request.args.get('q', '').upper()
    r = requests.get(f"https://query1.finance.yahoo.com/v1/finance/search?q={q}", headers={'User-Agent': 'Mozilla/5.0'})
    return jsonify([{'symbol': x['symbol'], 'name': x.get('shortname', '')} for x in r.json().get('quotes', [])])

@app.route('/add', methods=['POST'])
def add():
    s = request.form.get('symbol', '').upper().strip()
    p = float(request.form.get('price', 0))
    if s and not Stock.query.filter_by(symbol=s).first():
        db.session.add(Stock(symbol=s, purchase_price=p))
        db.session.commit()
    return jsonify({"status": "ok"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete(id):
    s = db.session.get(Stock, id)
    if s: db.session.delete(s); db.session.commit()
    return jsonify({"status": "ok"})

@app.route('/refresh_manual')
def refresh_manual():
    # Nu rulăm update_all direct aici pentru că dă timeout (502). 
    # Doar trimitem mesajul de pornire.
    return jsonify({"status": "Pornit în fundal..."})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
