from flask import Blueprint, render_template, current_app, request, redirect, jsonify

import datetime
import json
import time

home_bp = Blueprint('home_bp', __name__, template_folder='template', static_folder='static', static_url_path='/home-static')

@home_bp.route('/')
def home():    
    return render_template('home.html')