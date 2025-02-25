import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, session
# import yfinance as yf  # Removed yfinance
from datetime import datetime
from functools import wraps
import threading
import time
import os
import requests
from bs4 import BeautifulSoup  # Import BeautifulSoup
import tenacity
from typing import Optional, List


app = Flask(__name__)
app.secret_key = 'your_very_secret_key'  # CHANGE THIS!

# --- Database Functions ---

def create_connection():
    try:
        conn = sqlite3.connect('portfolio.db')
        return conn
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        flash("Database connection error", "error")  # Show to user
        return None

def create_table():
    conn = create_connection()
    if conn:
        with conn:  # Use context manager for automatic commit/close
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        quantity INTEGER NOT NULL,
                        price REAL NOT NULL,
                        transaction_type TEXT NOT NULL,
                        date TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS stock_prices (
                        symbol TEXT PRIMARY KEY,
                        current_price REAL NOT NULL,
                        last_updated TEXT NOT NULL
                    );
                """)
            except sqlite3.Error as e:
                print(f"Database error: {e}")
                flash("Database table creation error", "error")

create_table()  # Call when app starts

# --- Login Required Decorator ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('You need to be logged in.', 'warning')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


# --- Price Update Function (Background Thread) ---

# Major US exchanges to try.
US_EXCHANGES = ["NASDAQ", "NYSE", "AMEX", "OTC"]

@tenacity.retry(
    stop=tenacity.stop_after_attempt(5),
    wait=tenacity.wait_exponential(multiplier=1, min=4, max=10),
    retry=tenacity.retry_if_exception_type(Exception),  # General exception for network issues
    reraise=True
)
def fetch_price(symbol: str, exchanges: List[str] = US_EXCHANGES) -> Optional[float]:
    """Fetches the stock price from Google Finance, trying multiple US exchanges."""
    for exchange in exchanges:
        try:
            url = f"https://www.google.com/finance/quote/{symbol}:{exchange}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            price_element = soup.find('div', attrs={'class': 'YMlKec fxKbKc'})

            if price_element:
                price_text = price_element.get_text(strip=True).replace(',', '')
                if price_text[0].isalpha() == False:
                    price_text = price_text[1:]
                return float(price_text)  # Return ONLY the price

            alt_price_element = soup.find('div', attrs={'class': 'JwB6zf'})
            if alt_price_element:
                price_text = alt_price_element.get_text(strip=True).replace(',', '')
                if price_text[0].isalpha() == False:
                    price_text = price_text[1:]
                return float(price_text)


        except requests.exceptions.RequestException as e:
            print(f"Request error for {symbol}:{exchange}: {e}")
            continue
        except Exception as e:
            print(f"Error parsing price for {symbol}:{exchange}: {e}")
            continue

    print(f"Could not find price for {symbol} on any specified exchange.")
    return None  # Return only None
def update_prices():
    """Fetches and updates stock prices in the database using BeautifulSoup."""
    while True:  # Run continuously
        conn = create_connection()
        if not conn:
            print("update_prices: DB connection failed.")
            time.sleep(60)
            continue

        try:
            with conn:  # Use context manager
                cur = conn.cursor()
                cur.execute("SELECT DISTINCT symbol FROM transactions")
                symbols = [row[0] for row in cur.fetchall()]

                for symbol in symbols:
                    try:
                        current_price = fetch_price(symbol)  # Use fetch_price
                        if current_price is not None:  # Check for None
                            cur.execute('''
                                INSERT INTO stock_prices (symbol, current_price, last_updated)
                                VALUES (?, ?, ?)
                                ON CONFLICT(symbol) DO UPDATE SET
                                current_price = excluded.current_price,
                                last_updated = excluded.last_updated
                            ''', (symbol, current_price, datetime.now().isoformat()))
                            print(f"Updated price for {symbol}: {current_price}")
                        else:
                            print(f"Failed to update price for {symbol}") # Log failure
                    except Exception as e:
                        print(f"Error fetching/updating price for {symbol}: {e}")
        except sqlite3.Error as e:
            print(f"Database error in update_prices: {e}")
            conn.rollback()
        finally:
            if conn:  # Always close the connection
                conn.close()

        time.sleep(300)  # 5 minutes


# --- Start Threads ---
price_update_thread = threading.Thread(target=update_prices)
price_update_thread.daemon = True
price_update_thread.start()



@app.route('/')
def home():
    if 'logged_in' in session:
        return render_template('home.html')
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        session['logged_in'] = True
        session['username'] = request.form.get('username') # Optional
        flash('Logged in successfully!', 'success')
        next_url = request.args.get('next')
        return redirect(next_url or url_for('home'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    flash('Logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/index', methods=['GET', 'POST'])
@login_required
def index():
    conn = create_connection()
    if not conn:
        flash("Database connection error.", "error")
        return render_template('transactions.html', transactions=[])

    if request.method == 'POST':
        symbol = request.form['symbol'].upper()
        quantity_str = request.form['quantity']
        price_str = request.form['price']
        transaction_type = request.form['transaction_type']
        date_str = request.form['date']

        if not all([symbol, quantity_str, price_str, transaction_type, date_str]):
            flash("All fields are required.", "error")
            return redirect(url_for('index'))

        try:
            quantity = int(quantity_str)
            price = float(price_str)
            date = datetime.strptime(date_str, '%Y-%m-%d').date()
            if quantity <=0 or price <=0:
                flash("Quantity and Price must be positive.", "error")
                return redirect(url_for('index'))
        except ValueError:
            flash("Invalid input. Check quantity, price, and date.", "error")
            return redirect(url_for('index'))
        if conn:
          with conn:
            try:
                cur = conn.cursor()
                cur.execute('''
                    INSERT INTO transactions(symbol, quantity, price, transaction_type, date)
                    VALUES (?, ?, ?, ?, ?)
                ''', (symbol, quantity, price, transaction_type, date))
                flash("Transaction added!", "success")
                return redirect(url_for('index')) # Redirect after POST
            except sqlite3.Error as e:
                print(f"DB Error: {e}")
                conn.rollback()
                flash(f"DB Error: {e}", 'error')
                return redirect(url_for('index'))  # Redirect on error too.
        else:
          flash("Database connection error", "error")
          return redirect(url_for('index'))

    with conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM transactions")
            transactions = cur.fetchall()
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            flash("Error retrieving transactions.", "error")
            transactions = []  # Initialize to empty list
    if conn:
        conn.close()  # Explicitly close
    return render_template('transactions.html', transactions=transactions)



@app.route('/dashboard')
@login_required
def dashboard():
    portfolio = calculate_portfolio()

    chart_data = {
        'labels': [],
        'allocation': [],
        'profit_loss': [],
        'background_colors': [],
        'border_colors': []
    }
    colors = [ # Colors
        'rgba(255, 99, 132, 0.8)', 'rgba(54, 162, 235, 0.8)', 'rgba(255, 206, 86, 0.8)',
        'rgba(75, 192, 192, 0.8)', 'rgba(153, 102, 255, 0.8)', 'rgba(255, 159, 64, 0.8)',
        'rgba(23, 165, 137, 0.8)', 'rgba(230, 120, 70, 0.8)', 'rgba(150, 80, 180, 0.8)',
        'rgba(80, 200, 190, 0.8)', 'rgba(50, 75, 20, 0.8)', 'rgba(100, 150, 255, 0.8)',
        'rgba(200, 50, 100, 0.8)', 'rgba(100, 200, 50, 0.8)', 'rgba(50, 100, 200, 0.8)',
        'rgba(200, 100, 50, 0.8)', 'rgba(50, 200, 100, 0.8)', 'rgba(100, 50, 200, 0.8)',
        'rgba(255, 0, 0, 0.8)', 'rgba(0, 255, 0, 0.8)', 'rgba(0, 0, 255, 0.8)'
    ]

    total_value = sum(data['current_value'] for data in portfolio.values())

    for i, (symbol, data) in enumerate(portfolio.items()):
        if data['quantity'] > 0:
            chart_data['labels'].append(symbol)
            chart_data['allocation'].append(round((data['current_value'] / total_value) * 100, 2) if total_value > 0 else 0)
            chart_data['profit_loss'].append(round(data['profit_loss'], 2))
            chart_data['background_colors'].append(colors[i % len(colors)])
            chart_data['border_colors'].append(colors[i % len(colors)].replace('0.8', '1'))

    return render_template('dashboard.html', portfolio=portfolio, chart_data=chart_data)



def get_current_price(symbol):
    """Fetches the current price from the database."""
    conn = create_connection()
    if not conn:
        print("get_current_price: DB connection failed.")
        return None

    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT current_price FROM stock_prices WHERE symbol = ?", (symbol,))
            result = cur.fetchone()
            if result:
                return result[0]  # Extract the price
            else:
                print(f"No price in DB for {symbol}")
                return None
    except sqlite3.Error as e:
        print(f"DB error getting price for {symbol}: {e}")
        return None
    finally:
        if conn:
            conn.close()

def calculate_portfolio():
    conn = create_connection()
    if not conn:
        print("calculate_portfolio: DB connection failed.")
        return {}

    portfolio = {}
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT symbol, quantity, price, transaction_type FROM transactions")
            rows = cur.fetchall()
            for row in rows:
                symbol, quantity, price, transaction_type = row
                if symbol not in portfolio:
                    portfolio[symbol] = {'quantity': 0, 'total_cost': 0, 'transactions': []}
                portfolio[symbol]['transactions'].append({'quantity': quantity, 'price': price, 'type': transaction_type})
                if transaction_type == 'buy':
                    portfolio[symbol]['quantity'] += quantity
                    portfolio[symbol]['total_cost'] += quantity * price
                elif transaction_type == 'sell':
                    if portfolio[symbol]['quantity'] >= quantity:
                        portfolio[symbol]['quantity'] -= quantity
                        portfolio[symbol]['total_cost'] -= quantity * price
                    else:
                        print(f"Attempted to sell more {symbol} than owned.")
                        flash(f"You don't own enough {symbol} to sell.", "error")

        for symbol, data in portfolio.items():
            if data['quantity'] > 0:
                data['average_cost'] = data['total_cost'] / data['quantity'] if data['quantity'] > 0 else 0
                current_price = get_current_price(symbol)  # Get price from DB
                if current_price is not None:
                    #print(f"Current price for {symbol}: {current_price}")
                    data['current_value'] = data['quantity'] * current_price
                    data['profit_loss'] = data['current_value'] - data['total_cost']
                    data['profit_loss_percent'] = ((data['current_value'] - data['total_cost']) / data['total_cost']) * 100 if data['total_cost'] > 0 else 0
                    data['current_price'] = current_price
                    total_change_percent = 0
                    for transaction in data['transactions']:
                        if transaction['type'] == 'buy':
                            total_change_percent += ((current_price - transaction['price']) / transaction['price']) * 100 * (transaction['quantity'] / data['quantity'])
                        elif transaction['type'] == 'sell':
                            total_change_percent -= ((current_price - transaction['price']) / transaction['price']) * 100 * (transaction['quantity'] / data['quantity'])
                    data['change_percent'] = total_change_percent
                else:
                    data['current_value'] = 0
                    data['profit_loss'] = 0
                    data['profit_loss_percent'] = 0
                    data['current_price'] = None
                    data['change_percent'] = 0
            else:
                data['average_cost'] = data['current_value'] = data['profit_loss'] = 0
                data['profit_loss_percent'] = data['current_price'] = data['change_percent'] = 0

    except sqlite3.Error as e:
        print(f"Database error in calculate_portfolio: {e}")
        flash("Error calculating portfolio.", "error")
        return {}
    finally:
        if conn:
            conn.close()
    return portfolio

@app.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_transaction(id):
    conn = create_connection()
    if conn is None:
        return redirect(url_for('index'))

    with conn:
        if request.method == 'POST':
            symbol = request.form['symbol'].upper()
            quantity_str = request.form['quantity']
            price_str = request.form['price']
            transaction_type = request.form['transaction_type']
            date_str = request.form['date']

            if not symbol or not quantity_str or not price_str or not transaction_type or not date_str:
                flash("All fields are required.", "error")
                return redirect(url_for('edit_transaction', id=id))

            try:
                quantity = int(quantity_str)
                if quantity <= 0:
                    flash("Quantity must be a positive integer.", "error")
                    return redirect(url_for('edit_transaction', id=id))
            except ValueError:
                flash("Invalid quantity.  Must be an integer.", "error")
                return redirect(url_for('edit_transaction', id=id))

            try:
                price = float(price_str)
                if price <= 0:
                    flash("Price must be a positive number.", "error")
                    return redirect(url_for('edit_transaction', id=id))
            except ValueError:
                flash("Invalid price. Must be a number.", "error")
                return redirect(url_for('edit_transaction', id=id))

            try:
                date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                flash("Invalid date format. Use YYYY-MM-DD.", "error")
                return redirect(url_for('edit_transaction', id=id))

            try:
                cur = conn.cursor()
                cur.execute('''
                    UPDATE transactions
                    SET symbol = ?, quantity = ?, price = ?, transaction_type = ?, date = ?
                    WHERE id = ?
                ''', (symbol, quantity, price, transaction_type, date, id))
                conn.commit()
                flash("Transaction updated successfully!", "success")
                return redirect(url_for('index'))
            except sqlite3.Error as e:
                print(f"Database error: {e}")
                conn.rollback()
                flash("Error updating transaction.", "error")
                return redirect(url_for('edit_transaction', id=id))

        else:  # GET request: display edit form
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM transactions WHERE id = ?", (id,))
                transaction = cur.fetchone()

                if transaction:
                    transaction_dict = {
                        'id': transaction[0],
                        'symbol': transaction[1],
                        'quantity': transaction[2],
                        'price': transaction[3],
                        'transaction_type': transaction[4],
                        'date': transaction[5]
                    }
                    return render_template('edit.html', transaction=transaction_dict)
                else:
                    flash("Transaction not found.", "error")
                    return redirect(url_for('index'))
            except sqlite3.Error as e:
                print(f"Database error: {e}")
                flash("Error retrieving transaction.", "error")
                return redirect(url_for('index'))
            finally: # Ensure connection is closed
                if conn:
                    conn.close()

@app.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete_transaction(id):
    conn = create_connection()
    if conn:
        with conn:
            try:
                cur = conn.cursor()
                cur.execute("DELETE FROM transactions WHERE id = ?", (id,))
                flash("Transaction deleted!", "success")
            except sqlite3.Error as e:
                print(f"Database error: {e}")
                conn.rollback()
                flash("Error deleting transaction.", "error")
    else:
        flash("Database connection error", "error")
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080)) # Get port from env variable or 8080
    app.run(debug=False, host="0.0.0.0", port=port)