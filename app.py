from flask import (
    Flask,
    render_template,
    render_template_string,
    redirect,
    url_for,
    request,
    flash,
    session,
    jsonify,
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    login_user,
    login_required,
    logout_user,
    UserMixin,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
import random
import datetime
import uuid
import base64

# --- App setup ---
app = Flask(__name__)
app.config["SECRET_KEY"] = "your-secret-key"
# SQLite database file will be created in project root
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///grinapay.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Limit request size (helps avoid "Request Entity Too Large")
# 16 MB here — tune as needed
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

# Where uploaded files will be stored (inside static)
app.config["UPLOAD_FOLDER"] = os.path.join("static", "uploads")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
# selfies subfolder
os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], "selfies"), exist_ok=True)

# Allowed file extensions for uploads
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


# --- Database models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_type = db.Column(db.String(20), default="user")  # 'user' or 'merchant'
    username = db.Column(db.String(150), unique=True, nullable=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    # store path relative to static/, e.g. "uploads/avatar.jpg" or "images/profile.png"
    profile_pic = db.Column(db.String(250), default="images/profile.png")
    account_number = db.Column(db.String(12), unique=True, nullable=False)

    # Personal info for users
    first_name = db.Column(db.String(150))
    last_name = db.Column(db.String(150))
    dob = db.Column(db.String(50))
    bvn = db.Column(db.String(50))

    # Personal info for merchants
    company_name = db.Column(db.String(150))

    # Relationship with transactions
    transactions = db.relationship("Transaction", backref="user", lazy=True)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    txn_type = db.Column(db.String(50), nullable=False)  # Deposit, Withdrawal, Transfer, Betting Funding
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    description = db.Column(db.String(200), nullable=True)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_file_get_static_path(file_storage):
    """
    Save an uploaded FileStorage to the UPLOAD_FOLDER with a unique filename.
    Return the path relative to static/ (e.g. "uploads/uniqname.jpg") or None.
    """
    if file_storage and getattr(file_storage, "filename", None) and allowed_file(file_storage.filename):
        filename = secure_filename(file_storage.filename)
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        dest = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
        file_storage.save(dest)
        return os.path.join("uploads", unique_name)
    return None


def save_base64_selfie_get_static_path(data_url):
    """
    Accepts a base64 data URL like "data:image/png;base64,AAAA..."
    Decodes and saves it to static/uploads/selfies/<unique>.png
    Returns path relative to static (e.g. "uploads/selfies/<file>.png") or None.
    """
    if not data_url:
        return None
    if not data_url.startswith("data:image"):
        return None
    try:
        header, encoded = data_url.split(",", 1)
    except ValueError:
        return None

    # choose extension from header (png/jpeg)
    if "image/png" in header:
        ext = "png"
    elif "image/jpeg" in header or "image/jpg" in header:
        ext = "jpg"
    else:
        ext = "png"  # fallback

    try:
        img_bytes = base64.b64decode(encoded)
    except Exception:
        return None

    filename = f"selfie_{uuid.uuid4().hex}.{ext}"
    selfies_folder = os.path.join(app.config["UPLOAD_FOLDER"], "selfies")
    os.makedirs(selfies_folder, exist_ok=True)
    full_path = os.path.join(selfies_folder, filename)

    try:
        with open(full_path, "wb") as f:
            f.write(img_bytes)
    except Exception:
        return None

    return os.path.join("uploads", "selfies", filename)


# ---------- ROUTES ----------
@app.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        account_type = request.form.get("account_type") or "user"
        email = request.form.get("email")
        raw_password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        # Basic validation
        if not email or not raw_password:
            flash("Please provide email and password.", "danger")
            return redirect(url_for("signup"))

        if confirm_password and raw_password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("signup"))

        # duplicate email check
        if User.query.filter_by(email=email).first():
            flash("Email already registered!", "danger")
            return redirect(url_for("signup"))

        # create password hash and account number
        password = generate_password_hash(raw_password)
        account_number = str(random.randint(100000000000, 999999999999))

        # First try to save a file upload for profile_pic (if user used file input)
        profile_pic_path = save_file_get_static_path(request.files.get("profile_pic"))

        # Next, if a base64 selfie was provided via hidden input (from camera), save it and use as profile pic
        selfie_data = request.form.get("selfie")
        if not profile_pic_path and selfie_data:
            selfie_saved = save_base64_selfie_get_static_path(selfie_data)
            if selfie_saved:
                profile_pic_path = selfie_saved

        # Use default if no profile picture provided
        if not profile_pic_path:
            profile_pic_path = "images/profile.png"

        # Build user object depending on account_type
        if account_type == "user":
            new_user = User(
                account_type="user",
                email=email,
                password=password,
                account_number=account_number,
                profile_pic=profile_pic_path,
                first_name=request.form.get("first_name"),
                last_name=request.form.get("last_name"),
                dob=request.form.get("dob"),
                bvn=request.form.get("bvn"),
                username=request.form.get("username") or None,
            )
        elif account_type == "merchant":
            new_user = User(
                account_type="merchant",
                email=email,
                password=password,
                account_number=account_number,
                profile_pic=profile_pic_path,
                company_name=request.form.get("company_name"),
                username=request.form.get("username") or None,
            )
        else:
            flash("Invalid account type!", "danger")
            return redirect(url_for("signup"))

        # Save user
        db.session.add(new_user)
        db.session.commit()

        # Add a welcome deposit transaction
        welcome_txn = Transaction(
            user_id=new_user.id,
            txn_type="Deposit",
            amount=1000.0,
            description="Welcome bonus",
        )
        db.session.add(welcome_txn)
        db.session.commit()

        flash("Account created successfully! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        remember = True if request.form.get("remember") == "on" else False
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user, remember=remember)
            return redirect(url_for("dashboard"))
        flash("Invalid credentials!", "danger")
    return render_template("login.html")


@app.route("/dashboard")
@login_required
def dashboard():
    # load user's transactions and compute balances
    transactions = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.date).all()
    total_balance = 0.0
    for txn in transactions:
        if txn.txn_type == "Deposit":
            total_balance += txn.amount
        else:
            # Withdrawal and Transfer are treated as outflows here
            total_balance -= txn.amount

    metrics = {
        "total_balance": total_balance,
        "customers_count": 50 if current_user.account_type == "merchant" else None,
        "transactions_count": len(transactions),
        "loans_total": 8000,
        "savings_total": 3000,
    }

    # show only the last 5 transactions on dashboard
    last5 = transactions[-5:] if transactions else []

    # prepare profile pic URL for template (handles default image too)
    profile_pic_url = url_for("static", filename=current_user.profile_pic)

    return render_template(
        "dashboard.html",
        metrics=metrics,
        transactions=last5,
        account_number=current_user.account_number,
        profile_pic_url=profile_pic_url,
        username=current_user.username or current_user.email,
        account_type=current_user.account_type,
    )


@app.route("/transactions")
@login_required
def transactions_page():
    transactions = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.date.desc()).all()
    profile_pic_url = url_for("static", filename=current_user.profile_pic)
    return render_template(
        "transactions.html",
        transactions=transactions,
        account_number=current_user.account_number,
        profile_pic_url=profile_pic_url,
        username=current_user.username or current_user.email,
        account_type=current_user.account_type,
    )


@app.route("/savings")
@login_required
def savings():
    # Dummy data for now, replace with real DB queries later
    history = [
        {"date": "2025-09-10", "type": "Savings – 3 Months", "amount": "₦50,000", "status": "Active"},
        {"date": "2025-07-01", "type": "Investment – Medium Risk", "amount": "₦100,000", "status": "Pending"},
    ]
    return render_template("savings_investment.html", history=history, username=current_user.username)


# ----------- NEW TRANSACTION ROUTES -----------
@app.route("/deposit", methods=["GET", "POST"])
@login_required
def deposit():
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount"))
        except (TypeError, ValueError):
            flash("Invalid amount", "danger")
            return redirect(url_for("deposit"))
        txn = Transaction(user_id=current_user.id, txn_type="Deposit", amount=amount, description="Manual deposit")
        db.session.add(txn)
        db.session.commit()
        flash("Deposit successful!", "success")
        return redirect(url_for("transactions_page"))
    return render_template("deposit.html")


@app.route("/withdraw", methods=["GET", "POST"])
@login_required
def withdraw():
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount"))
        except (TypeError, ValueError):
            flash("Invalid amount", "danger")
            return redirect(url_for("withdraw"))

        balance = sum(tx.amount if tx.txn_type == "Deposit" else -tx.amount for tx in current_user.transactions)
        if amount > balance:
            flash("Insufficient funds!", "danger")
            return redirect(url_for("withdraw"))

        txn = Transaction(user_id=current_user.id, txn_type="Withdrawal", amount=amount, description="Cash withdrawal")
        db.session.add(txn)
        db.session.commit()
        flash("Withdrawal successful!", "success")
        return redirect(url_for("transactions_page"))
    return render_template("withdraw.html")


@app.route("/transfer", methods=["GET", "POST"])
@login_required
def transfer():
    if request.method == "POST":
        target_account = request.form.get("target_account")
        try:
            amount = float(request.form.get("amount"))
        except (TypeError, ValueError):
            flash("Invalid amount", "danger")
            return redirect(url_for("transfer"))

        recipient = User.query.filter_by(account_number=target_account).first()
        if not recipient:
            flash("Recipient account not found!", "danger")
            return redirect(url_for("transfer"))

        balance = sum(tx.amount if tx.txn_type == "Deposit" else -tx.amount for tx in current_user.transactions)
        if amount > balance:
            flash("Insufficient funds!", "danger")
            return redirect(url_for("transfer"))

        sender_txn = Transaction(
            user_id=current_user.id,
            txn_type="Transfer",
            amount=amount,
            description=f"Transfer to {recipient.account_number}",
        )
        recipient_txn = Transaction(
            user_id=recipient.id,
            txn_type="Deposit",
            amount=amount,
            description=f"Transfer from {current_user.account_number}",
        )
        db.session.add(sender_txn)
        db.session.add(recipient_txn)
        db.session.commit()
        flash("Transfer successful!", "success")
        return redirect(url_for("transactions_page"))
    return render_template("transfer.html")


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        # update username with uniqueness check
        new_username = request.form.get("username")
        if new_username:
            # if username changed, ensure not already taken by another user
            existing = User.query.filter(User.username == new_username, User.id != current_user.id).first()
            if existing:
                flash("Username already taken. Choose another.", "danger")
                return redirect(url_for("profile"))
            current_user.username = new_username

        # update other fields depending on account type
        if current_user.account_type == "user":
            current_user.first_name = request.form.get("first_name")
            current_user.last_name = request.form.get("last_name")
            current_user.dob = request.form.get("dob")
            current_user.bvn = request.form.get("bvn")
        elif current_user.account_type == "merchant":
            current_user.company_name = request.form.get("company_name")

        # handle profile picture upload (if provided)
        file = request.files.get("profile_pic")
        if file and file.filename:
            saved_path = save_file_get_static_path(file)
            if saved_path:
                current_user.profile_pic = saved_path  # e.g. "uploads/unique.jpg"

        # commit changes to DB
        db.session.commit()
        flash("Profile updated successfully!", "success")
        return redirect(url_for("profile"))

    profile_pic_url = url_for("static", filename=current_user.profile_pic)
    return render_template(
        "profile.html",
        user=current_user,
        account_number=current_user.account_number,
        profile_pic_url=profile_pic_url,
        username=current_user.username or current_user.email,
        account_type=current_user.account_type,
    )


# ---------------------------
# Scaffolding feature routes
# ---------------------------
@app.route("/add-money", methods=["GET", "POST"])
@login_required
def add_money():
    if request.method == "POST":
        flash("Add money action received (placeholder).", "info")
        return redirect(url_for("dashboard"))
    return render_template_string("""
      {% extends "dashboard.html" %}
      {% block content %}
      <div class="p-6">
        <h1 class="text-2xl font-bold">Add Money</h1>
        <p class="text-gray-600">Placeholder page — implement payment gateway or funding method here.</p>
      </div>
      {% endblock %}
    """)

@app.route("/bills")
@login_required
def bills():
    return render_template("bill_payment.html")


# -------- UPDATED BETTING ROUTE --------
@app.route("/betting", methods=["GET", "POST"])
@login_required
def betting():
    betting_companies = [
        "Bet9ja", "NairaBet", "SportyBet", "BetKing", "MerryBet",
        "1xBet", "SureBet247", "AccessBet", "BetWay", "LivescoreBet"
    ]

    if request.method == "POST":
        company = request.form.get("company")
        account_id = request.form.get("account_id")
        try:
            amount = float(request.form.get("amount"))
        except (TypeError, ValueError):
            flash("Invalid amount.", "danger")
            return redirect(url_for("betting"))

        # calculate balance from transactions
        balance = sum(
            tx.amount if tx.txn_type == "Deposit" else -tx.amount
            for tx in current_user.transactions
        )
        if amount > balance:
            flash("Insufficient funds to fund betting account.", "danger")
            return redirect(url_for("betting"))

        txn = Transaction(
            user_id=current_user.id,
            txn_type="Betting Funding",
            amount=amount,
            description=f"Funded {company} account ({account_id})",
        )
        db.session.add(txn)
        db.session.commit()

        flash(f"Successfully funded {company} account!", "success")
        return redirect(url_for("transactions_page"))

    # render the nice betting page
    return render_template("betting.html", betting_companies=betting_companies)


@app.route("/internet", methods=["GET", "POST"])
@login_required
def internet():
    providers = ["MTN", "GLO", "AIRTEL", "ETISALAT"]

    if request.method == "POST":
        phone_number = request.form.get("phone_number")
        bundle = request.form.get("bundle")
        payment_method = request.form.get("payment_method")

        if not phone_number or not bundle or not payment_method:
            flash("All fields are required.", "danger")
            return redirect(url_for("internet"))

        # Example logic (adjust with your transaction model)
        txn = Transaction(
            user_id=current_user.id,
            txn_type="Data Purchase",
            amount=float(bundle.split("₦")[-1].replace(",", "").strip()),
            description=f"Bought {bundle} for {phone_number} on {payment_method}",
        )
        db.session.add(txn)
        db.session.commit()

        flash(f"Successfully purchased {bundle} for {phone_number}", "success")
        return redirect(url_for("transactions_page"))

    return render_template("internet.html", providers=providers)


# Airtime page route
@app.route("/airtime")
@login_required
def airtime():
    return render_template("airtime.html")


@app.route("/education")
@login_required
def education():
    return render_template("education.html")


@app.route("/cards")
@login_required
def cards():
    return render_template_string("""
      {% extends "dashboard.html" %}
      {% block content %}
      <div class="p-6">
        <h1 class="text-2xl font-bold">Cards</h1>
        <p class="text-gray-600">Card management (placeholder).</p>
      </div>
      {% endblock %}
    """)

@app.route("/more")
@login_required
def more():
    return render_template_string("""
      {% extends "dashboard.html" %}
      {% block content %}
      <div class="p-6">
        <h1 class="text-2xl font-bold">More</h1>
        <p class="text-gray-600">Additional services and settings (placeholder).</p>
      </div>
      {% endblock %}
    """)

@app.route("/send", methods=["GET", "POST"])
@login_required
def send():
    if request.method == "POST":
        flash("Send action received (placeholder).", "info")
        return redirect(url_for("dashboard"))
    return render_template_string("""
      {% extends "dashboard.html" %}
      {% block content %}
      <div class="p-6">
        <h1 class="text-2xl font-bold">Send</h1>
        <p class="text-gray-600">Send money to recipients (placeholder).</p>
      </div>
      {% endblock %}
    """)

@app.route("/pay", methods=["GET", "POST"])
@login_required
def pay():
    if request.method == "POST":
        flash("Pay action received (placeholder).", "info")
        return redirect(url_for("dashboard"))
    return render_template_string("""
      {% extends "dashboard.html" %}
      {% block content %}
      <div class="p-6">
        <h1 class="text-2xl font-bold">Pay</h1>
        <p class="text-gray-600">Pay bills and merchants (placeholder).</p>
      </div>
      {% endblock %}
    """)

@app.route("/home")
@login_required
def home_alias():
    return redirect(url_for("dashboard"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------- RUN APP ----------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
