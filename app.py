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
    try: requests.get(url, timeout=10)
    except: print("Eroare Telegram")

def get_last_session_data(symbol):
    try:
        # Creăm o sesiune care imită un browser real
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
        
        ticker = yf.Ticker(symbol, session=session)
        # Prindem ultimele 7 zile pentru siguranță
        hist = ticker.history(period="7d")
        
        if hist.empty:
            return None
        
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
            if stock.last_alert_date == today: continue
            data = get_last_session_data(stock.symbol)
            if not data: continue
            
            current = data['current']
            high = data['high']
            alert = False
            msg = ""

            if current <= stock.purchase_price * 0.95:
                msg = f"⚠️ {stock.symbol}: -5% vs achizitie\nAcum: {current:.2f}$"
                alert = True
            elif current <= high * 0.85:
                msg = f"📉 {stock.symbol}: -15% vs maxim zi\nMaxim: {high:.2f}$\nAcum: {current:.2f}$"
                alert = True

            if alert:
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
                'peak': round(data['high'], 2), 'current': round(data['current'], 2), 'date': data['date']
            })
        else:
            results.append({'id': s.id, 'symbol': s.symbol, 'buy': s.purchase_price, 'peak': "N/A", 'current': 0, 'date': "N/A"})
    return render_template('index.html', stocks=results)

@app.route('/search')
def search_stock():
    query = request.args.get('q')
    if not query: return jsonify([])
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}"
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        data = resp.json()
        return jsonify([{'symbol': q['symbol'], 'name': q.get('shortname', '')} for q in data.get('quotes', []) if q.get('quoteType') in ['EQUITY', 'ETF']])
    except: return jsonify([])

@app.route('/add', methods=['POST'])
def add_stock():
    symbol = request.form.get('symbol').upper()
    price = request.form.get('price')
    if not Stock.query.filter_by(symbol=symbol).first():
        db.session.add(Stock(symbol=symbol, purchase_price=float(price)))
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
