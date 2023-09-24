from flask import Flask
from flask_cors import CORS
from flask_apscheduler import APScheduler

# import eventlet

import os

from .bp.home.routes import home_bp

# eventlet.monkey_patch(thread=True, time=True)

def create_app():
    app = Flask(__name__)
    CORS(app)

    uri_db = 'mysql+pymysql://root:@localhost/katro?charset=utf8mb4'
    # uri_db = 'mysql+pymysql://inonaryc_katrouser:katropassword666@inonary.com/inonaryc_katro?charset=utf8mb4'
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL_KATRO', uri_db)
    app.secret_key = "secret_key"
    app.config['PERMANENT_SESSION_LIFETIME'] = 360000
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    app.register_blueprint(home_bp)

    with app.app_context() :
        # from .models import db, convert_table_character
        from .socket import socketio
        
        # db.init_app(app)
        # db.create_all()
        # convert_table_character()

        socketio.init_app(app)

        scheduler = APScheduler()
        scheduler.init_app(app)
        scheduler.start()

        return app, socketio