import os
import psycopg2
import psycopg2.extras
import argparse
from datetime import date
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

# Load environment variables securely
APP_SECRET = os.environ.get("APP_SECRET", "dev-secret-change-me")
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable not set")

app = Flask(__name__)
app.secret_key = APP_SECRET

# ---------- Database helpers ----------

def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor, sslmode='require')
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    """Initialize database tables and create default admin user"""
    with app.app_context():
        db = get_db()
        cur = db.cursor()
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        if not os.path.exists(schema_path):
            raise FileNotFoundError("schema.sql file not found")
        with open(schema_path, "r") as f:
            cur.execute(f.read())
        # Create default admin if not exists
        cur.execute("SELECT id FROM users WHERE email = %s", ("admin@milk.local",))
        if cur.fetchone() is None:
            cur.execute("""
                INSERT INTO users (name, email, password_hash, role)
                VALUES (%s, %s, %s, %s)
            """, ("Admin", "admin@milk.local", generate_password_hash("admin123"), "admin"))
            print("Default admin created: admin@milk.local / admin123")
        else:
            print("Default admin already exists.")
        db.commit()
        print("Database initialized.")

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
                flash("Not authorized.", "error")
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ---------- Routes ----------

@app.route("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("admin_dashboard" if session.get("role") == "admin" else "user_dashboard"))
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        role = request.form["role"]
        if not name or not email or not password or role not in ["admin", "user"]:
            flash("Please fill all fields correctly.", "error")
            return redirect(url_for("register"))
        db = get_db()
        cur = db.cursor()
        try:
            cur.execute("""
                INSERT INTO users (name, email, password_hash, role)
                VALUES (%s, %s, %s, %s)
            """, (name, email, generate_password_hash(password), role))
            db.commit()
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))
        except psycopg2.errors.UniqueViolation:
            db.rollback()
            flash("Email already registered.", "error")
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['role'] = user['role']
            flash("Login successful!", "success")
            return redirect(url_for('home'))
        else:
            flash("Invalid email or password", "error")
    
    return render_template('login.html')


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM users WHERE id = %s", (session["user_id"],))
    user = cur.fetchone()
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not name or not email:
            flash("Name and email are required.", "error")
            return redirect(url_for("profile"))
        try:
            cur.execute("UPDATE users SET name=%s, email=%s WHERE id=%s", (name, email, session["user_id"]))
            if new_password:
                if new_password != confirm:
                    flash("Passwords do not match.", "error")
                    return redirect(url_for("profile"))
                cur.execute("UPDATE users SET password_hash=%s WHERE id=%s",
                            (generate_password_hash(new_password), session["user_id"]))
            db.commit()
            session["name"] = name
            flash("Profile updated.", "success")
        except psycopg2.errors.UniqueViolation:
            db.rollback()
            flash("Email already exists.", "error")
    return render_template("profile.html", user=user)

@app.route("/user/dashboard")
@login_required
@role_required("user")
def user_dashboard():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM deliveries WHERE user_id = %s ORDER BY date DESC", (session["user_id"],))
    deliveries = cur.fetchall()
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
    cur = db.cursor()
    cur.execute("""
        INSERT INTO deliveries (user_id, date, liters)
        VALUES (%s, %s, %s)
        ON CONFLICT(user_id, date) DO UPDATE SET liters=EXCLUDED.liters
    """, (session["user_id"], d, liters))
    db.commit()
    flash("Delivery updated.", "success")
    return redirect(url_for("user_dashboard"))

@app.route("/user/delivery/<int:delivery_id>/delete", methods=["POST"])
@login_required
@role_required("user")
def delete_delivery(delivery_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM deliveries WHERE id = %s AND user_id = %s", (delivery_id, session["user_id"]))
    db.commit()
    flash("Deleted.", "success")
    return redirect(url_for("user_dashboard"))

@app.route("/admin")
@login_required
@role_required("admin")
def admin_dashboard():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()["count"]
    cur.execute("SELECT COALESCE(SUM(total_liters), 0) FROM productions")
    total_production = cur.fetchone()["coalesce"]
    cur.execute("SELECT COUNT(*) FROM deliveries")
    total_deliveries = cur.fetchone()["count"]
    stats = {
        "total_users": total_users,
        "total_production": total_production,
        "total_deliveries": total_deliveries
    }
    return render_template("admin_dashboard.html", **stats)

@app.route("/admin/production", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin_production():
    db = get_db()
    cur = db.cursor()
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
        cur.execute("""
            INSERT INTO productions (date, total_liters)
            VALUES (%s, %s)
            ON CONFLICT(date) DO UPDATE SET total_liters=EXCLUDED.total_liters
        """, (d, total))
        db.commit()
        flash("Production updated.", "success")
        return redirect(url_for("admin_production"))
    cur.execute("SELECT * FROM productions ORDER BY date DESC")
    productions = cur.fetchall()
    return render_template("admin_production.html", productions=productions, today=date.today().isoformat())

@app.route("/admin/production/<int:production_id>/delete", methods=["POST"])
@login_required
@role_required("admin")
def delete_production(production_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM productions WHERE id = %s", (production_id,))
    db.commit()
    flash("Deleted.", "success")
    return redirect(url_for("admin_production"))

@app.route("/admin/deliveries")
@login_required
@role_required("admin")
def admin_deliveries():
    picked_date = request.args.get("date", "").strip()
    db = get_db()
    cur = db.cursor()
    if picked_date:
        cur.execute("""
            SELECT u.name, COALESCE(d.liters, 0) AS liters
            FROM users u
            LEFT JOIN deliveries d ON u.id = d.user_id AND d.date = %s
            WHERE u.role = 'user'
            ORDER BY u.name
        """, (picked_date,))
        breakdown = cur.fetchall()
    else:
        breakdown = []
    cur.execute("""
        SELECT d.date, u.name, d.liters
        FROM deliveries d
        JOIN users u ON u.id = d.user_id
        ORDER BY d.date DESC, u.name ASC
        LIMIT 200
    """)
    all_deliveries = cur.fetchall()
    return render_template("admin_deliveries.html", breakdown=breakdown, picked_date=picked_date, all_deliveries=all_deliveries)

# ---------- Command-line interface ----------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-db", action="store_true", help="Initialize the database")
    args = parser.parse_args()

    if args.init_db:
        init_db()
    else:
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port, debug=False)
