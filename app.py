import os
import datetime
import requests
import yfinance as yf
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///portfolio.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

TELEGRAM_TOKEN = os.environ.get('TG_TOKEN')
TELEGRAM_ID = os.environ.get('TG_ID')

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, nullable=False)
    last_alert_date = db.Column(db.String(20), default="")

with app.app_context():
    db.create_all()

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={TELEGRAM_ID}&text={message}"
    try: requests.get(url)
    except: print("Eroare Telegram")

def get_last_session_data(symbol):
    try:
        ticker = yf.Ticker(symbol)
        # Luam ultimele 5 zile pentru a fi siguri ca prindem ultima sesiune (chiar si dupa weekend/sarbatori)
        hist = ticker.history(period="5d")
        
        if hist.empty:
            return None
        
        # Selectam strict ULTIMA linie (ultima zi de tranzactionare incheiata sau in curs)
        last_day = hist.iloc[-1]
        
        return {
            "current": last_day['Close'],
            "high": last_day['High'],
            "date": hist.index[-1].strftime('%d-%m-%Y')
        }
    except Exception as e:
        print(f"Eroare yfinance pentru {symbol}: {e}")
        return None

def check_prices():
    with app.app_context():
        stocks = Stock.query.all()
        today = datetime.date.today().isoformat()
        
        for stock in stocks:
            if stock.last_alert_date == today:
                continue

            data = get_last_session_data(stock.symbol)
            if not data: continue

            current_price = data['current']
            last_day_high = data['high']
            
            alert_triggered = False
            msg = ""

            # Alerta 5% fata de pretul tau de achizitie (Constantin)
            if current_price <= stock.purchase_price * 0.95:
                msg = f"⚠️ {stock.symbol}: Scadere >5% fata de achizitie!\nAchizitie: {stock.purchase_price:.2f}$\nPret actual: {current_price:.2f}$"
                alert_triggered = True
            
            # Alerta 15% fata de MAXIMUL ULTIMEI ZILE de tranzactionare
            elif current_price <= last_day_high * 0.85:
                msg = f"📉 {stock.symbol}: Scadere >15% fata de maximul ultimei zile ({data['date']})!\nMaxim zi: {last_day_high:.2f}$\nPret actual: {current_price:.2f}$"
                alert_triggered = True

            if alert_triggered:
                send_telegram_msg(msg)
                stock.last_alert_date = today
                db.session.commit()

scheduler = BackgroundScheduler()
scheduler.add_job(func=check_prices, trigger="interval", minutes=15)
scheduler.start()

@app.route('/')
def index():
    stocks = Stock.query.all()
    results = []
    for s in stocks:
        data = get_last_session_data(s.symbol)
        if data:
            results.append({
                'id': s.id, 'symbol': s.symbol, 'buy': s.purchase_price,
                'peak': round(data['high'], 2), 
                'current': round(data['current'], 2),
                'date': data['date']
            })
        else:
            results.append({
                'id': s.id, 'symbol': s.symbol, 'buy': s.purchase_price,
                'peak': "N/A", 'current': 0, 'date': "N/A"
            })
    return render_template('index.html', stocks=results)

@app.route('/search')
def search_stock():
    query = request.args.get('q')
    if not query: return jsonify([])
    url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}"
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = resp.json()
        output = [{'symbol': q['symbol'], 'name': q.get('shortname', q.get('longname', ''))} 
                  for q in data.get('quotes', []) if q.get('quoteType') in ['EQUITY', 'ETF']]
        return jsonify(output)
    except: return jsonify([])

@app.route('/add', methods=['POST'])
def add_stock():
    symbol = request.form.get('symbol').upper()
    price = request.form.get('price')
    if not Stock.query.filter_by(symbol=symbol).first():
        new_stock = Stock(symbol=symbol, purchase_price=float(price))
        db.session.add(new_stock)
        db.session.commit()
    return jsonify({"status": "ok"})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete_stock(id):
    stock = Stock.query.get(id)
    if stock:
        db.session.delete(stock)
        db.session.commit()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
