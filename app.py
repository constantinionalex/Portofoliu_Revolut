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

def get_stock_data(symbol):
    try:
        ticker = yf.Ticker(symbol)
        # Folosim period="7d" pentru a acoperi weekend-ul
        # Intervalul "1d" este cel mai stabil pentru date istorice recente
        hist = ticker.history(period="7d", interval="1d")
        
        if hist.empty or len(hist) < 1:
            return None
        
        # Luam ultimul pret de inchidere disponibil (chiar daca e de vineri)
        current_price = hist['Close'].iloc[-1]
        # Varful este maximul din ultimele zile de tranzactionare gasite
        peak_period = hist['High'].max()
        
        return {"current": current_price, "peak": peak_period}
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

            data = get_stock_data(stock.symbol)
            if not data: continue

            current_price = data['current']
            peak_val = data['peak']
            
            alert_triggered = False
            msg = ""

            # Alerta 5% fata de achizitie
            if current_price <= stock.purchase_price * 0.95:
                msg = f"⚠️ {stock.symbol}: Scadere >5% fata de achizitie!\nAchizitie: {stock.purchase_price:.2f}$\nActual (Ultimul): {current_price:.2f}$"
                alert_triggered = True
            # Alerta 15% fata de varf
            elif current_price <= peak_val * 0.85:
                msg = f"📉 {stock.symbol}: Scadere >15% fata de varful recent!\nVarf detectat: {peak_val:.2f}$\nActual (Ultimul): {current_price:.2f}$"
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
        data = get_stock_data(s.symbol)
        if data:
            results.append({
                'id': s.id, 'symbol': s.symbol, 'buy': s.purchase_price,
                'peak': round(data['peak'], 2), 'current': round(data['current'], 2)
            })
        else:
            results.append({
                'id': s.id, 'symbol': s.symbol, 'buy': s.purchase_price,
                'peak': "N/A", 'current': 0 
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
