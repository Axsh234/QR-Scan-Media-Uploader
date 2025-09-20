from flask import Flask, render_template, redirect, url_for, request, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import cloudinary
import cloudinary.uploader
import io, zipfile, requests
from config import Config

# ----------------- App Setup -----------------
app = Flask(__name__)
app.config.from_object(Config)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

cloudinary.config(
    cloud_name=app.config['CLOUDINARY_CLOUD_NAME'],
    api_key=app.config['CLOUDINARY_API_KEY'],
    api_secret=app.config['CLOUDINARY_API_SECRET']
)

# ----------------- Models -----------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Media(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(300), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    public_id = db.Column(db.String(500), nullable=False)
    is_visible = db.Column(db.Boolean, default=True)
    uploaded_by = db.Column(db.String(150), nullable=True)
    description = db.Column(db.String(500), nullable=True)  # metadata field
    size = db.Column(db.Integer, nullable=True)
    mimetype = db.Column(db.String(50), nullable=True)

# ----------------- User Loader -----------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ----------------- Auth Routes -----------------
@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """Always allow creating/resetting the admin user."""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            flash("Username and password cannot be empty.")
            return render_template('setup.html')

        existing = User.query.filter_by(username=username).first()
        if existing:
            db.session.delete(existing)
            db.session.commit()

        hashed = generate_password_hash(password)
        admin = User(username=username, password=hashed)
        db.session.add(admin)
        db.session.commit()

        flash(f"Admin '{username}' created! You can now log in.")
        return redirect(url_for('login'))

    return render_template('setup.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Create additional users."""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            flash("Username and password cannot be empty.")
            return redirect(url_for('register'))

        if User.query.filter_by(username=username).first():
            flash("That username is already taken.")
            return redirect(url_for('register'))

        hashed = generate_password_hash(password)
        new_user = User(username=username, password=hashed)
        db.session.add(new_user)
        db.session.commit()
        flash(f"User '{username}' created successfully. You can now log in.")
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if not User.query.first():
        return redirect(url_for('setup'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('manage'))
        flash("Invalid credentials")

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('gallery'))

# ----------------- Media Routes -----------------
@app.route('/')
def home():
    # Redirect to the upload page
    return redirect(url_for('upload'))

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        file = request.files.get('file')  # now there's only one input

        if file and file.filename:
            uploader_name = current_user.username if current_user.is_authenticated else request.form.get('uploader_name', 'Anonymous')

            # Reset pointer for size calculation
            file.seek(0)
            size = len(file.read())
            file.seek(0)

            try:
                # Upload to Cloudinary
                result = cloudinary.uploader.upload(file)
            except Exception as e:
                flash(f"Upload failed: {str(e)}")
                return redirect(url_for('upload'))

            media = Media(
                filename=file.filename,
                url=result['secure_url'],
                public_id=result['public_id'],
                uploaded_by=uploader_name,
                description=request.form.get('description'),
                size=size,
                mimetype=file.mimetype
            )

            db.session.add(media)
            db.session.commit()
            flash("Upload successful!")
            return redirect(url_for('gallery'))

    return render_template('upload.html')





@app.route('/gallery')
def gallery():
    medias = Media.query.filter_by(is_visible=True).all()
    return render_template('gallery.html', medias=medias)

from flask import request, jsonify

@app.route('/manage')
@login_required
def manage():
    medias = Media.query.all()
    return render_template('manage.html', medias=medias)


@app.route('/delete/<int:media_id>')
@login_required
def delete(media_id):
    media = Media.query.get(media_id)
    if media:
        cloudinary.uploader.destroy(media.public_id)
        db.session.delete(media)
        db.session.commit()
        flash("Deleted successfully")
    return redirect(url_for('manage'))


@app.route('/toggle_visibility/<int:media_id>')
@login_required
def toggle_visibility(media_id):
    media = Media.query.get(media_id)
    if media:
        media.is_visible = not media.is_visible
        db.session.commit()
    return redirect(url_for('manage'))


@app.route('/download_selected', methods=['POST'])
@login_required
def download_selected():
    selected_ids = request.form.getlist('media_ids')
    if not selected_ids:
        flash("No files selected")
        return redirect(url_for('manage'))

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w') as zf:
        for media_id in selected_ids:
            media = Media.query.get(int(media_id))
            if media:
                response = requests.get(media.url)
                zf.writestr(media.filename, response.content)

    memory_file.seek(0)
    return send_file(
        memory_file,
        download_name='selected_media.zip',
        as_attachment=True
    )


# -------------------- New Bulk Operations -------------------- #

@app.route('/bulk_toggle_visibility', methods=['POST'])
@login_required
def bulk_toggle_visibility():
    data = request.get_json()
    media_ids = data.get('media_ids', [])
    for m_id in media_ids:
        media = Media.query.get(m_id)
        if media:
            media.is_visible = not media.is_visible
            db.session.add(media)
    db.session.commit()
    return jsonify({'status': 'success'})


@app.route('/bulk_delete', methods=['POST'])
@login_required
def bulk_delete():
    data = request.get_json()
    media_ids = data.get('media_ids', [])
    for m_id in media_ids:
        media = Media.query.get(m_id)
        if media:
            cloudinary.uploader.destroy(media.public_id)
            db.session.delete(media)
    db.session.commit()
    return jsonify({'status': 'success'})

# ----------------- Run App -----------------
#if __name__ == '__main__':
#   with app.app_context():
 #       db.create_all()
 #   app.run(debug=True)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
