import os
import sqlite3
import argparse
from datetime import date
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

APP_SECRET = os.environ.get("APP_SECRET", "dev-secret-change-me")
DB_PATH = os.path.join(os.path.dirname(__file__), "milkman.db")

app = Flask(__name__)
app.secret_key = APP_SECRET

# ---------- Database helpers ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        with open(os.path.join(os.path.dirname(__file__), "schema.sql"), "r") as f:
            db.executescript(f.read())
        # Create default admin if not exists
        cur = db.execute("SELECT id FROM users WHERE email = ?", ("admin@milk.local",))
        if not cur.fetchone():
            db.execute("INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
                       ("Admin", "admin@milk.local", generate_password_hash("admin123"), "admin"))
        db.commit()

# ---------- Authentication helpers ----------
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def role_required(role):
    from functools import wraps
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if session.get("role") != role:
                flash("Not authorized", "error")
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ---------- Routes ----------

@app.route("/")
def home():
    if session.get("user_id"):
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        else:
            return redirect(url_for("user_dashboard"))
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        role = request.form["role"]
        if not name or not email or not password or role not in ["admin", "user"]:
            flash("Please fill all fields.", "error")
            return redirect(url_for("register"))
        db = get_db()
        try:
            db.execute("INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
                       (name, email, generate_password_hash(password), role))
            db.commit()
            flash("Account created. Please login.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Email already registered.", "error")
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["name"] = user["name"]
            flash("Welcome " + user["name"], "success")
            return redirect(url_for("admin_dashboard" if user["role"] == "admin" else "user_dashboard"))
        flash("Invalid credentials.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not name or not email:
            flash("Name and email are required.", "error")
            return redirect(url_for("profile"))
        try:
            db.execute("UPDATE users SET name=?, email=? WHERE id=?", (name, email, session["user_id"]))
            if new_password:
                if new_password != confirm:
                    flash("Passwords do not match.", "error")
                    return redirect(url_for("profile"))
                db.execute("UPDATE users SET password_hash=? WHERE id=?",
                           (generate_password_hash(new_password), session["user_id"]))
            db.commit()
            session["name"] = name
            flash("Profile updated.", "success")
        except sqlite3.IntegrityError:
            flash("Email already exists.", "error")
    return render_template("profile.html", user=user)

# ---------- User routes ----------

@app.route("/user/dashboard")
@login_required
@role_required("user")
def user_dashboard():
    db = get_db()
    deliveries = db.execute("SELECT * FROM deliveries WHERE user_id = ? ORDER BY date DESC",
                            (session["user_id"],)).fetchall()
    return render_template("user_dashboard.html", deliveries=deliveries, today=date.today().isoformat())

@app.route("/user/delivery", methods=["POST"])
@login_required
@role_required("user")
def add_delivery():
    d = request.form["date"]
    liters = request.form["liters"]
    try:
        liters = float(liters)
        if liters < 0:
            raise ValueError()
    except:
        flash("Enter valid liters.", "error")
        return redirect(url_for("user_dashboard"))
    db = get_db()
    db.execute("INSERT INTO deliveries (user_id, date, liters) VALUES (?, ?, ?) ON CONFLICT(user_id, date) DO UPDATE SET liters=excluded.liters",
               (session["user_id"], d, liters))
    db.commit()
    flash("Saved.", "success")
    return redirect(url_for("user_dashboard"))

@app.route("/user/delivery/<int:delivery_id>/delete", methods=["POST"])
@login_required
@role_required("user")
def delete_delivery(delivery_id):
    db = get_db()
    db.execute("DELETE FROM deliveries WHERE id=? AND user_id=?", (delivery_id, session["user_id"]))
    db.commit()
    flash("Deleted.", "success")
    return redirect(url_for("user_dashboard"))

# ---------- Admin routes ----------

@app.route("/admin")
@login_required
@role_required("admin")
def admin_dashboard():
    db = get_db()
    stats = {
        "total_users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "total_production": db.execute("SELECT COALESCE(SUM(total_liters), 0) FROM productions").fetchone()[0],
        "total_deliveries": db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
    }
    return render_template("admin_dashboard.html", **stats)

@app.route("/admin/production", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin_production():
    db = get_db()
    if request.method == "POST":
        d = request.form["date"]
        total = request.form["total_liters"]
        try:
            total = float(total)
            if total < 0:
                raise ValueError()
        except:
            flash("Enter valid liters.", "error")
            return redirect(url_for("admin_production"))
        db.execute("INSERT INTO productions (date, total_liters) VALUES (?, ?) ON CONFLICT(date) DO UPDATE SET total_liters=excluded.total_liters",
                   (d, total))
        db.commit()
        flash("Saved.", "success")
        return redirect(url_for("admin_production"))
    productions = db.execute("SELECT * FROM productions ORDER BY date DESC").fetchall()
    return render_template("admin_production.html", productions=productions, today=date.today().isoformat())

@app.route("/admin/production/<int:production_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_production(production_id):
    db = get_db()
    db.execute("DELETE FROM productions WHERE id=?", (production_id,))
    db.commit()
    flash("Deleted.", "success")
    return redirect(url_for("admin_production"))

@app.route("/admin/deliveries")
@login_required
@role_required("admin")
def admin_deliveries():
    picked_date = request.args.get("date", "").strip()
    db = get_db()
    if picked_date:
        breakdown = db.execute("""
            SELECT u.name, COALESCE(d.liters, 0) AS liters
            FROM users u
            LEFT JOIN deliveries d ON u.id = d.user_id AND d.date = ?
            WHERE u.role = 'user'
            ORDER BY u.name
        """, (picked_date,)).fetchall()
    else:
        breakdown = []
    all_deliveries = db.execute("""
        SELECT d.date, u.name, d.liters
        FROM deliveries d
        JOIN users u ON u.id = d.user_id
        ORDER BY d.date DESC, u.name ASC
        LIMIT 200
    """).fetchall()
    return render_template("admin_deliveries.html", breakdown=breakdown, picked_date=picked_date, all_deliveries=all_deliveries)

# ---------- CLI ----------

if __name__ == "__main__":
    import os
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--init-db", action="store_true", help="Initialize the database")
    args = parser.parse_args()

    if args.init_db:
        init_db()
        print("Database initialized with default admin.")
    else:
        if not os.path.exists(DB_PATH):
            init_db()
            print("Database auto-initialized.")
        # Use PORT from environment (Render assigns it dynamically)
        port = int(os.environ.get("PORT", 5000))
        # Listen on all IPs for web access
        app.run(host='0.0.0.0', port=port, debug=False)