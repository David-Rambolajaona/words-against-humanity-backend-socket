from flask_socketio import SocketIO, emit, send, join_room, leave_room
from flask import request, current_app, g as gs
from apscheduler.schedulers.background import BackgroundScheduler
from flask_apscheduler import APScheduler
from apscheduler.jobstores.base import JobLookupError

import gevent
import eventlet

import json
import datetime
import random
import string

from .questions import qa

eventlet.monkey_patch(thread=True, time=True)

socketio = SocketIO(cors_allowed_origins="*")

games = {}

DATE_FORMAT = "%d/%m/%Y, %H:%M:%S"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TIME_LOADING_START = 3
TIME_QUESTION = 180
TIME_LOADING_QUESTION = 2
TIME_VOTE_PER_PERSON = 15
TIME_LOADING_VOTE = 2
TIME_RESULT_VOTE_PER_PERSON = 8
TIME_RANKING = 20

def get_date_now() :
    date_now = datetime.datetime.utcnow()
    date_str = date_now.strftime(DATE_FORMAT)
    return date_now, date_str

def generate_random_letters(nb) :
    letters = ''
    for i in range(nb) :
        letters += random.choice(string.ascii_letters)
    return letters

def get_people(game_code) :
    if game_code in games.keys() :
        g = games.get(game_code)
        people = []
        for sid in g.get("people", {}).get("connected", {}).keys() :
            person = g.get("people", {}).get("connected", {}).get(sid)
            people.append(person)
        people.sort(key=lambda x: x["score"], reverse=True)
        people_cleaned = []
        for p in people :
            p_cleaned = dict(p)
            del p_cleaned["date_enter"]
            people_cleaned.append(p_cleaned)
        return people_cleaned
    return []

def get_data_phase(game_code) :
    if game_code in games.keys() :
        data_phase = dict(games[game_code].get("data_phase", {}))
        if "answers" in data_phase.keys() :
            del data_phase["answers"]
        return data_phase
    return {}

def get_res_join_game(game_code) :
    if game_code in games.keys() :
        g = games[game_code]
        return {
            "people": get_people(game_code),
            "host_pseudo": g.get("host_pseudo"),
            "phase": g.get("phase"),
            "data_phase": get_data_phase(game_code),
            "data_game": g.get("data_game"),
            "round": g.get("round"),
            "question": g.get("question"),
            "answer": g.get("answer"),
        }
    return None

def get_person(game_code, sid) :
    res = {}
    if game_code in games.keys() :
        if sid in games[game_code]["people"]["connected"].keys() :
            res = games[game_code]["people"]["connected"][sid]
        elif sid in games[game_code]["people"]["disconnected"].keys() :
            res = games[game_code]["people"]["disconnected"][sid]
    return res

def get_qa() :
    q_a = random.choice(qa)
    return q_a

def do_next_phase(game_code, app = None) :
    if game_code in games.keys() :
        g = games[game_code]
        phase = g.get("phase")
        date_now, date_str = get_date_now()

        if not app :
            app = current_app._get_current_object()

        if phase == "loading_start" :
            date_end = date_now + datetime.timedelta(seconds=TIME_QUESTION)
            date_end_str = date_end.strftime(DATE_FORMAT)
            games[game_code]["phase"] = "question"

            q_a = get_qa()

            games[game_code]["question"] = q_a.get("q")
            games[game_code]["answer"] = q_a.get("a")
            games[game_code]["data_phase"] = {
                "date_end": date_end_str,
                "answers": {},
                "answers_list": []
            }
            # Scheduling job for next phase
            schedule_job_for_next_phase(game_code, "question", TIME_QUESTION, app = app)
        elif phase == "question" :
            games[game_code]["phase"] = "loading_question"

            # Scheduling job for next phase
            schedule_job_for_next_phase(game_code, "loading_question", TIME_LOADING_QUESTION, app = app)
        elif phase == "loading_question" :
            answer_sid_list = list(g["data_phase"]["answers"].keys())
            random.shuffle(answer_sid_list)
            answer_list = []
            for answer_sid in answer_sid_list :
                answer_list.append({
                    "answer": g["data_phase"]["answers"].get(answer_sid, {}).get("answer"),
                    "sid": answer_sid
                })
            games[game_code]["data_phase"]["answers_list"] = answer_list
            games[game_code]["data_phase"]["voting_people"] = answer_sid_list

            time_vote = (TIME_VOTE_PER_PERSON * len(answer_list)) + TIME_VOTE_PER_PERSON
            date_end = date_now + datetime.timedelta(seconds=time_vote)
            date_end_str = date_end.strftime(DATE_FORMAT)
            games[game_code]["data_phase"]["date_end"] = date_end_str

            games[game_code]["phase"] = "vote"
            # Scheduling job for next phase
            schedule_job_for_next_phase(game_code, "vote", time_vote, app = app)
        elif phase == "vote" :
            games[game_code]["phase"] = "loading_vote"

            # Scheduling job for next phase
            schedule_job_for_next_phase(game_code, "loading_vote", TIME_LOADING_VOTE, app = app)
        elif phase == "loading_vote" :
            games[game_code]["phase"] = "result_vote"
            ranking = []
            for person_sid, answer in games[game_code]["data_phase"]["answers"].items() :
                ranking.append({
                    "sid": person_sid,
                    "nb_votes": len(answer.get("votes")),
                    "points": 0,
                    "pseudo": get_person(game_code, person_sid).get("pseudo"),
                    "answer": answer.get("answer")
                })
            ranking.sort(key=lambda x: x["nb_votes"], reverse=True)
            right_voting_people = []
            winning_people = []
            max_votes = 0
            for person in ranking :
                if len(winning_people) == 0 and person["nb_votes"] == 0 :
                    ranking = []
                    break
                if person["nb_votes"] < max_votes :
                    break
                max_votes = person["nb_votes"]
                winning_people.append(person["sid"])
                right_voting_people.extend(games[game_code]["data_phase"]["answers"][person["sid"]]["votes"])
            for i in range(len(ranking)) :
                sid = ranking[i]["sid"]
                if ranking[i]["sid"] in winning_people :
                    ranking[i]["points"] += max_votes + 1
                    if sid in games[game_code]["people"]["connected"].keys() :
                        games[game_code]["people"]["connected"][sid]["score"] += max_votes + 1
                if ranking[i]["sid"] in right_voting_people :
                    ranking[i]["points"] += max_votes
                    if sid in games[game_code]["people"]["connected"].keys() :
                        games[game_code]["people"]["connected"][sid]["score"] += max_votes
            ranking.sort(key=lambda x: (x["nb_votes"], x["points"]), reverse=True)
            res_winning_people = {
                "firsts": [],
                "last": None
            }
            for person_sid in winning_people :
                res_winning_people["firsts"].append({
                    "sid": person_sid,
                    "pseudo": get_person(game_code, person_sid).get("pseudo")
                })
            if len(winning_people) > 1 :
                res_winning_people["last"] = res_winning_people["firsts"][-1]
                del res_winning_people["firsts"][-1]

            time_result = (TIME_RESULT_VOTE_PER_PERSON * len(ranking)) + TIME_RESULT_VOTE_PER_PERSON
            date_end = date_now + datetime.timedelta(seconds=time_result)
            date_end_str = date_end.strftime(DATE_FORMAT)
            games[game_code]["data_phase"] = {
                "ranking": ranking,
                "winning_people": res_winning_people,
                "max_votes": max_votes,
                "date_end": date_end_str,
                "finished": False
            }
            # Check if game is over
            max_score = 0
            min_score = 0
            people = get_people(game_code)
            if len(people) > 0 :
                max_score = people[0].get("score")
                if max_score != people[-1].get("score") :
                    min_score = people[-1].get("score")
            if max_score >= games[game_code]["data_game"].get("score_goal", 0) :
                games[game_code]["data_phase"]["finished"] = True
                date_finish = date_end + datetime.timedelta(seconds=TIME_RANKING)
                date_finish_str = date_finish.strftime(DATE_FORMAT)
                games[game_code]["data_phase"]["date_finish"] = date_finish_str
                winners = []
                losers = []
                for person in people :
                    if person.get("score") == max_score :
                        winners.append(person)
                    elif person.get("score") == min_score :
                        losers.append(person)
                games[game_code]["data_phase"]["winners"] = winners
                games[game_code]["data_phase"]["losers"] = losers
                for person in winners :
                    sid = person.get("sids")[0]
                    games[game_code]["people"]["connected"][sid]["awards"].append("win")
                for person in losers :
                    sid = person.get("sids")[0]
                    games[game_code]["people"]["connected"][sid]["awards"].append("lose")
            # Otherwise, new round
            else :
                # Scheduling job for next phase
                schedule_job_for_next_phase(game_code, "result_vote", time_result+2, app = app)
        elif phase == "result_vote" :
            games[game_code]["phase"] = "loading_start"
            games[game_code]["round"] += 1
            games[game_code]["data_phase"] = {}
            # Scheduling job for next phase
            schedule_job_for_next_phase(game_code, "loading_start", TIME_LOADING_START, app = app)

        res = {
            "type": "res_next_phase",
            "success": True,
            "data": get_res_join_game(game_code)
        }
        
        with app.app_context() :
            emit('message', json.dumps(res), room=game_code, namespace='/')

def job_for_next_phase(app, game_code, phase, salt) :
    if game_code in games.keys() and games[game_code].get("phase") == phase and games[game_code].get("salt") == salt :
        do_next_phase(game_code, app)

def schedule_job_for_next_phase(game_code, phase, time_sec, app = None) :
    global games
    salt = generate_random_letters(8)
    games[game_code]["salt"] = salt
    date_now_job = datetime.datetime.now()
    date_job_start = date_now_job + datetime.timedelta(seconds=time_sec)
    if not app :
        app = current_app._get_current_object()
    app.apscheduler.add_job(func=job_for_next_phase, trigger='date', run_date=date_job_start, args=[app, game_code, phase, salt], id='j'+phase+game_code+salt)

@socketio.on('disconnect')
def on_disconnect() :
    global games
    # Searching this user from games
    game_code = None
    origin_sid = None
    player_data = {}
    for code, game_data in games.items() :
        for person_sid, person_data in game_data["people"]["connected"].items() :
            if request.sid in person_data["sids"] :
                game_code = code
                origin_sid = person_sid
                player_data = dict(person_data)
                break
        if game_code :
            break

    # If found, put the user to disconnected people
    if game_code :
        games[game_code]["people"]["disconnected"][origin_sid] = player_data
        del games[game_code]["people"]["connected"][origin_sid]
        leave_room(game_code)

        # New host
        new_host_sid = None
        new_host_pseudo = None
        if origin_sid == games[game_code].get("host_sid") and len(list(games[game_code]["people"]["connected"].keys())) > 0 :
            new_host_sid = list(games[game_code]["people"]["connected"].keys())[0]
            new_host_pseudo = games[game_code]["people"]["connected"][new_host_sid].get("pseudo")
            games[game_code]["host_sid"] = new_host_sid
            games[game_code]["host_pseudo"] = new_host_pseudo

        # Give the updated players to players
        res = {
            "type": "res_disconnection",
            "success": True,
            "people": get_people(game_code),
            "new_host": True if new_host_sid else False,
            "host_sid": new_host_sid,
            "host_pseudo": new_host_pseudo
        }
        emit('message', json.dumps(res), room=game_code)

        # Notify players
        data_msg = {
            "type": "disco",
            "from": origin_sid,
            "pseudo": player_data.get("pseudo")
        }
        res = {
        "type": "res_send_message",
            "success": True,
            "data": data_msg
        }
        emit('message', json.dumps(res), room=game_code)
        games[game_code]["chat"].append(data_msg)

        # Notify players about the new host
        if new_host_sid :
            data_msg = {
                "type": "new_host",
                "to": new_host_sid,
                "pseudo": new_host_pseudo
            }
            res = {
            "type": "res_send_message",
                "success": True,
                "data": data_msg
            }
            emit('message', json.dumps(res), room=game_code)
            games[game_code]["chat"].append(data_msg)

        # Delete the game if there is no user connected anymore
        if len(list(games[game_code]["people"]["connected"].keys())) == 0 :
            del games[game_code]

@socketio.on('join_game')
def on_join_game(data) :
    global games
    data = json.loads(data)
    game_code = data.get("game_code", '').upper()

    # Create new game if requested by client
    if data.get("is_host") and not data.get("origin_sid") :
        if game_code not in games.keys() :
            games[game_code] = {
                "people": {
                    "connected": {},
                    "disconnected": {}
                },
                "host_sid": request.sid,
                "host_pseudo": data.get("pseudo"),
                "phase": "start",
                "data_phase": {},
                "data_game": {
                    "score_goal": 10
                },
                "round": 1,
                "question": "",
                "answer": "",
                "salt": "",
                "chat": []
            }
    
    # Join game if it exists
    if game_code in games.keys() :
        # If origin_sid in disconnected people, move it to the connected ones
        # Otherwise, put this current user in connected people
        origin_sid = data.get("origin_sid")
        date_now, date_str = get_date_now()
        if origin_sid in games[game_code]["people"]["disconnected"].keys() :
            person_data = dict(games[game_code]["people"]["disconnected"][origin_sid])
            del games[game_code]["people"]["disconnected"][origin_sid]
            games[game_code]["people"]["connected"][origin_sid] = person_data
            games[game_code]["people"]["connected"][origin_sid]["sids"].append(request.sid)
        elif origin_sid in games[game_code]["people"]["connected"].keys() :
            # PUT CODE LATER !!!
            pass
        else :
            origin_sid = request.sid
            new_person = {
                "sids": [origin_sid],
                "pseudo": data.get("pseudo"),
                "score": 0,
                "awards": [],
                "date_enter": date_now
            }
            games[game_code]["people"]["connected"][origin_sid] = new_person
        join_room(game_code)
        res = {
            "type": "res_join_game",
            "success": True,
            "data": get_res_join_game(game_code),
            "chars": data.get("chars"),
            "origin_sid": origin_sid
        }
        emit('message', json.dumps(res), room=game_code)

        # Notify players
        data_msg = {
            "type": "reco",
            "from": origin_sid,
            "pseudo": games[game_code]["people"]["connected"].get(origin_sid, {}).get("pseudo")
        }
        res = {
        "type": "res_send_message",
            "success": True,
            "data": data_msg
        }
        emit('message', json.dumps(res), room=game_code)
        games[game_code]["chat"].append(data_msg)
    else :
        res = {
            "type": "res_join_game",
            "success": False
        }
        emit('message', json.dumps(res), broadcast=False)

@socketio.on('start_game')
def on_start_game(data) :
    global games
    data = json.loads(data)
    game_code = data.get("game_code")
    if game_code in games.keys() :
        if games[game_code]["phase"] == "start" or games[game_code]["phase"] == "result_vote" :
            games[game_code]["phase"] = "loading_start"
            games[game_code]["round"] = 1
            games[game_code]["data_game"] = data.get("data_game")
            games[game_code]["data_phase"] = {}
            for person_sid in games[game_code]["people"]["connected"].keys() :
                games[game_code]["people"]["connected"][person_sid]["score"] = 0
            for person_sid in games[game_code]["people"]["disconnected"].keys() :
                games[game_code]["people"]["disconnected"][person_sid]["score"] = 0

            # Scheduling job for next phase
            schedule_job_for_next_phase(game_code, "loading_start", TIME_LOADING_START, app = current_app._get_current_object())

            res = {
                "type": "res_start_game",
                "success": True,
                "data": get_res_join_game(game_code)
            }
            emit('message', json.dumps(res), room=game_code)
    else :
        res = {
            "type": "res_start_game",
            "success": False
        }
        emit('message', json.dumps(res), broadcast=False)

@socketio.on('send_answer')
def on_send_answer(data) :
    global games
    data = json.loads(data)
    game_code = data.get("game_code")
    if game_code in games.keys() :
        if games[game_code]["phase"] == "question" :
            games[game_code]["data_phase"]["answers"][data.get("from_sid")] = {
                "answer": data.get("answer"),
                "votes": [],
                "has_voted": False
            }
            
            # Check if everybody has sent answers
            everybody_has_sent = True
            nb_people_answered = 0
            for person_sid in games[game_code]["people"]["connected"].keys() :
                if person_sid not in games[game_code]["data_phase"]["answers"].keys() :
                    everybody_has_sent = False
                else :
                    nb_people_answered += 1

            # Notify players
            data_msg = {
                "type": "answer",
                "from": data.get("from_sid"),
                "pseudo": get_person(game_code, data.get("from_sid")).get("pseudo"),
                "meta": {
                    "nb": nb_people_answered,
                    "total": len(list(games[game_code]["people"]["connected"].keys()))
                }
            }
            res = {
            "type": "res_send_message",
                "success": True,
                "data": data_msg
            }
            emit('message', json.dumps(res), room=game_code)
            games[game_code]["chat"].append(data_msg)

            if everybody_has_sent :
                do_next_phase(game_code)

@socketio.on('vote_for')
def on_vote_for(data) :
    global games
    data = json.loads(data)
    game_code = data.get("game_code")
    if game_code in games.keys() :
        if games[game_code]["phase"] == "vote" :
            sid_to_vote = data.get("sid_to_vote")
            from_sid = data.get("from_sid")
            if sid_to_vote in games[game_code]["data_phase"]["answers"].keys() :
                games[game_code]["data_phase"]["answers"][sid_to_vote]["votes"].append(from_sid)
                if from_sid in games[game_code]["data_phase"]["answers"].keys() :
                    games[game_code]["data_phase"]["answers"][from_sid]["has_voted"] = True

                # Check if everybody has voted
                # If so, do next phase
                everybody_has_voted = True
                nb_people_voted = 0
                for person_sid in games[game_code]["data_phase"]["answers"].keys() :
                    if not games[game_code]["data_phase"]["answers"][person_sid].get("has_voted") :
                        everybody_has_voted = False
                    else :
                        nb_people_voted += 1

                # Notify players
                data_msg = {
                    "type": "vote",
                    "from": data.get("from_sid"),
                    "pseudo": get_person(game_code, data.get("from_sid")).get("pseudo"),
                    "meta": {
                        "nb": nb_people_voted,
                        "total": len(list(games[game_code]["data_phase"]["answers"].keys()))
                    }
                }
                res = {
                "type": "res_send_message",
                    "success": True,
                    "data": data_msg
                }
                emit('message', json.dumps(res), room=game_code)
                games[game_code]["chat"].append(data_msg)

                if everybody_has_voted :
                    do_next_phase(game_code)

@socketio.on('send_message')
def on_send_message(data) :
    global games
    data = json.loads(data)
    game_code = data.get("game_code")
    if game_code in games.keys() :
        games[game_code]["chat"].append(data.get("data_msg"))
        res = {
            "type": "res_send_message",
            "success": True,
            "data": data.get("data_msg")
        }
        emit('message', json.dumps(res), room=game_code)

@socketio.on('load_chat')
def on_load_chat(data) :
    global games
    data = json.loads(data)
    game_code = data.get("game_code")
    if game_code in games.keys() :
        NB_MSG_TO_LOAD = 50
        chat_loaded = []
        index_1 = 0
        
        if len(games[game_code]["chat"]) > 0 :
            if data.get("first_load") :
                index_2 = len(games[game_code]["chat"])
            else :
                index_2 = data.get("index_1", 1)
            index_1 = index_2 - NB_MSG_TO_LOAD
            index_1 = 0 if index_1 < 0 else index_1
            if index_2 > 0 :
                chat_loaded = games[game_code]["chat"][index_1:index_2]

        res = {
            "type": "res_load_chat",
            "success": True,
            "origin_sid": data.get("origin_sid"),
            "chat": chat_loaded,
            "index_1": index_1,
            "first_load": data.get("first_load", False)
        }
        emit('message', json.dumps(res))
    else :
        res = {
            "type": "res_load_chat",
            "success": False
        }
        emit('message', json.dumps(res))