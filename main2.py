# Enhanced Dank Memer Adventure Bot with Fixed Navigation, Choice Memory, and Keep-Alive
# Based on actual adventure scenarios and optimal choice patterns

import time
import os
import requests
import sys
import json
import random
import re
import pprint
from flask import Flask
from threading import Thread, Event
import queue
import pickle
from datetime import datetime, timedelta

# Ensure using websocket-client
try:
    from websocket import WebSocketApp
except ImportError:
    os.system("pip uninstall websocket -y && pip install websocket-client")
    from websocket import WebSocketApp

# Environment settings
TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
DANK_MEMER_ID = "270904126974590976"
GUILD_ID = "1398487811362918400"
INTERACTIVE_COMMANDS = ["pls adv"]
COMMAND_DELAY = 6     # Time between commands (seconds)
ROUND_DELAY = 240     # 4 minutes between rounds (seconds)
DELETE_MESSAGE_DELAY = 10  # Time to delete message after sending (seconds), increased from 3
STOP_FILE = "stop.txt"
CHOICE_MEMORY_FILE = "choice_memory.pkl"  # Choice memory file

# Enhanced interaction times for longer adventures
INTERACTION_MIN_DELAY = 3.5  # Minimum wait before clicking, increased for longer delays
INTERACTION_MAX_DELAY = 7.0  # Maximum wait before clicking, increased for longer delays
ADVENTURE_TIMEOUT = 600      # 10 minutes per adventure, increased for long adventures
INTERACTION_RETRY_DELAY = 2.0  # Wait time between interaction retries, slight increase
NAVIGATION_WAIT_TIME = 12    # Wait for navigation message after choice, increased for longer delays
NO_START_BUTTON_TIMEOUT = 20  # Wait for start button (seconds), increased for slow interaction

HEADERS = {
    "Authorization": TOKEN,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

API_BASE = "https://discord.com/api/v9"

# Global variables
waiting_for_interaction = False
waiting_for_navigation = False
waiting_for_start_button = False  # New variable for start button wait
session_id = None
adventure_start_time = None
current_ws = None
last_heartbeat = time.time()
last_choice_time = None
dynamic_round_delay = ROUND_DELAY
choice_memory = {}  # Choice memory
no_start_button_time = None  # Start button wait time
remaining_cooldown = 0  # Track remaining cooldown time

# ✅ Flask for UptimeRobot
app = Flask('')

@app.route('/')
def home():
    return "✅ Enhanced Adventure Bot is alive!"

@app.route('/health')
def health_check():
    return "OK", 200

def run_flask(port=8080):
    from waitress import serve
    import socket

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            serve(app, host='0.0.0.0', port=port)
            break
        except OSError as e:
            if e.errno == 98 and attempt < max_attempts - 1:  # Address already in use
                port += 1
                print(f"⚠️ Port {port-1} in use, trying port {port}...")
            else:
                raise

def keep_alive():
    thread = Thread(target=run_flask, args=(8080,))
    thread.daemon = True  # Ensure thread stops with main thread
    thread.start()
    print("🌐 Flask server started for keep-alive on port 8080 (or next available)")

# --- CHOICE MEMORY SYSTEM ---
def load_choice_memory():
    """Load choice memory from file"""
    global choice_memory
    try:
        if os.path.exists(CHOICE_MEMORY_FILE):
            with open(CHOICE_MEMORY_FILE, 'rb') as f:
                choice_memory = pickle.load(f)
            print(f"✅ Loaded {len(choice_memory)} remembered choices")
        else:
            choice_memory = {}
            print("📝 Starting with empty choice memory")
    except Exception as e:
        print(f"❌ Error loading choice memory: {e}")
        choice_memory = {}

def save_choice_memory():
    """Save choice memory to file"""
    try:
        with open(CHOICE_MEMORY_FILE, 'wb') as f:
            pickle.dump(choice_memory, f)
        print(f"💾 Saved {len(choice_memory)} choices to memory at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
    except Exception as e:
        print(f"❌ Error saving choice memory: {e}")

def create_scenario_key(content, embeds):
    """Create unique key for scenario"""
    all_text = content.lower()
    for embed in embeds:
        all_text += " " + str(embed.get("description", "")).lower()
        all_text += " " + str(embed.get("title", "")).lower()

    key_words = []
    important_keywords = [
        "alien", "probe", "spaceship", "planet", "toxic", "dangerous",
        "kitchen", "food", "telescope", "repair", "star", "fuel",
        "transmission", "signal", "blob", "radioactive", "chemicals", "odd eyes"
    ]

    for keyword in important_keywords:
        if keyword in all_text:
            key_words.append(keyword)

    if not key_words:
        return all_text[:50].strip()

    return "_".join(sorted(key_words))

def remember_choice(scenario_key, chosen_button_label, success_outcome):
    """Remember a specific choice for a scenario"""
    global choice_memory

    if scenario_key not in choice_memory:
        choice_memory[scenario_key] = {}

    if chosen_button_label not in choice_memory[scenario_key]:
        choice_memory[scenario_key][chosen_button_label] = {
            'success_count': 0,
            'failure_count': 0,
            'last_used': None
        }

    if success_outcome:
        choice_memory[scenario_key][chosen_button_label]['success_count'] += 1
    else:
        choice_memory[scenario_key][chosen_button_label]['failure_count'] += 1

    choice_memory[scenario_key][chosen_button_label]['last_used'] = datetime.now().isoformat()

    print(f"🧠 Remembered choice: {chosen_button_label} for scenario: {scenario_key[:30]}...")
    save_choice_memory()

def get_remembered_choice(scenario_key, available_buttons):
    """Get best remembered choice for a scenario"""
    if scenario_key not in choice_memory:
        return None

    best_button = None
    best_score = -1

    for button in available_buttons:
        button_label = button['label'].lower().strip()

        if not button_label or button_label == '':
            continue

        for remembered_label, stats in choice_memory[scenario_key].items():
            if (button_label == remembered_label.lower() or 
                button_label in remembered_label.lower() or 
                remembered_label.lower() in button_label):

                success_rate = stats['success_count'] / max(1, stats['success_count'] + stats['failure_count'])
                score = success_rate * 100 + stats['success_count']

                if score > best_score:
                    best_score = score
                    best_button = button
                    print(f"🧠 Found remembered choice: {button['label']} (score: {score:.1f})")

    return best_button

# --- RANDOM EVENT DETECTION ---
def is_random_event(content, embeds, components):
    """Determine if message is a random event from Dank Memer"""
    all_text = content.lower()

    for embed in embeds:
        all_text += " " + str(embed.get("title", "")).lower()
        all_text += " " + str(embed.get("description", "")).lower()
        if embed.get("fields"):
            for field in embed["fields"]:
                all_text += " " + str(field.get("name", "")).lower()
                all_text += " " + str(field.get("value", "")).lower()

    random_event_indicators = [
        "the shop sale just started",
        "i am very bored so here is a boring event",
        "let's see how big your knowledge is",
        "guess guess guess",
        "microsoft is trying to buy discord again",
        "skype is trying to beat discord again",
        "they've got airpods",
        "karen is starting a fight",
        "your immune system is under attack",
        "windows sucks lol",
        "lol imagine using skype",
        "jerk",
        "frick off karen",
        "disinfect",
        "trivia night",
        "let's see who's the smartest person here",
        "what an absolute gamer",
        "gamers are gaming in the game",
        "someone posted an idea on reddit",
        "try their game",
        "f in the chat i just died in minecraft",
        "press an f in the chat",
        "random event",
        "global event",
        "server event",
        "giveaway",
        "drop sale",
        "limited time",
        "guess the price",
        "what's the price",
        "price between",
        "health:",
        "hp:",
        "damage the",
        "defeat",
        "fight"
    ]

    for indicator in random_event_indicators:
        if indicator in all_text:
            print(f"🎲 Random event detected: '{indicator}'")
            return True

    if components:
        buttons = extract_all_buttons(components)
        button_labels = [btn.get("label", "").lower() for btn in buttons]

        event_button_patterns = [
            "f", "windows sucks lol", "disinfect", "jerk", "frick off karen", "lol imagine using skype"
        ]

        for pattern in event_button_patterns:
            if pattern in button_labels:
                print(f"🎲 Random event button detected: '{pattern}'")
                return True

    price_pattern = r'\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})?'
    if re.search(price_pattern, all_text) and ("guess" in all_text or "price" in all_text):
        print("🎲 Price guessing event detected")
        return True

    if "gained" in all_text and not any(indicator in all_text for indicator in random_event_indicators):
        print("🚫 'gained' detected but not a clear random event - ignoring as random event")
        return False

    return False

# --- ENHANCED BUTTON EXTRACTION ---
def extract_all_buttons(components):
    """Enhanced extraction of buttons from Discord component structure"""
    buttons = []

    print(f"🔍 Starting button search...")
    print(f"📋 Raw components: {json.dumps(components, indent=2)[:500]}...")

    if not components:
        print("❌ No components provided")
        return buttons

    def extract_from_component(comp, comp_index=0):
        if not isinstance(comp, dict):
            return

        comp_type = comp.get("type")
        print(f"🔧 Component {comp_index}: type={comp_type}")

        if comp_type == 1:
            components_list = comp.get("components", [])
            print(f"   🗂️ ActionRow with {len(components_list)} sub-components")

            for i, sub_comp in enumerate(components_list):
                if isinstance(sub_comp, dict) and sub_comp.get("type") == 2:
                    button_data = {
                        "custom_id": sub_comp.get("custom_id", ""),
                        "label": sub_comp.get("label", ""),
                        "style": sub_comp.get("style", 1),
                        "disabled": sub_comp.get("disabled", False),
                        "emoji": sub_comp.get("emoji", {}),
                        "url": sub_comp.get("url", ""),
                        "raw": sub_comp
                    }
                    buttons.append(button_data)
                    print(f"   🔘 Button {i}: '{button_data['label']}' (disabled: {button_data['disabled']})")

        elif comp_type == 2:
            button_data = {
                "custom_id": comp.get("custom_id", ""),
                "label": comp.get("label", ""),
                "style": comp.get("style", 1),
                "disabled": comp.get("disabled", False),
                "emoji": comp.get("emoji", {}),
                "url": comp.get("url", ""),
                "raw": comp
            }
            buttons.append(button_data)
            print(f"   🔘 Direct Button: '{button_data['label']}' (disabled: {button_data['disabled']})")

    if isinstance(components, list):
        for i, component in enumerate(components):
            extract_from_component(component, i)
    elif isinstance(components, dict):
        extract_from_component(components)

    print(f"✅ Total buttons found: {len(buttons)}")
    return buttons

# --- ADVENTURE MESSAGE FILTERING ---
def is_adventure_message(content, embeds, components):
    """Determine if message is adventure-related"""
    all_text = content.lower()

    for embed in embeds:
        all_text += " " + str(embed.get("title", "")).lower()
        all_text += " " + str(embed.get("description", "")).lower()
        if embed.get("fields"):
            for field in embed["fields"]:
                all_text += " " + str(field.get("name", "")).lower()
                all_text += " " + str(field.get("value", "")).lower()

    if is_random_event(content, embeds, components):
        print("🚫 Random event detected - ignoring")
        return False

    global waiting_for_interaction, waiting_for_navigation, waiting_for_start_button
    if waiting_for_interaction or waiting_for_navigation or waiting_for_start_button:
        print("🎮 In adventure mode - treating message as adventure content")
        return True

    clear_adventure_keywords = [
        "adventure",
        "spaceship",
        "space station",
        "planet",
        "galaxy",
        "alien",
        "what do you do",
        "you approach",
        "you encounter",
        "you came across",
        "choose items",
        "bring along",
        "recommended",
        "adventure summary",
        "adventure again in",
        "your adventure is over",
        "adventure completed",
        "adventure has ended",
        "turns out",
        "blob-like planet",
        "odd eyes",
        "kitchen"
    ]

    for keyword in clear_adventure_keywords:
        if keyword in all_text:
            print(f"✅ Adventure keyword detected: '{keyword}'")
            return True

    if components:
        buttons = extract_all_buttons(components)
        if buttons or any(comp.get("type") == 3 for comp in components):  # Check for select menus
            button_labels = [btn.get("label", "").lower() for btn in buttons]
            adventure_button_patterns = [">", "→", "inspect", "try", "approach", "take", "grab", "talk", "start"]
            non_adventure_button_patterns = ["basement", "bank", "couch", "identity theft", "gaslighting", "vandalism"]

            if any(pattern in " ".join(button_labels) for pattern in non_adventure_button_patterns):
                print(f"🚫 Non-adventure buttons detected: {button_labels}")
                return False

            if any(pattern in " ".join(button_labels) for pattern in adventure_button_patterns) or any(comp.get("type") == 3 for comp in components):
                print(f"✅ Adventure buttons or select menu detected: {button_labels}")
                return True

    print(f"❓ No clear adventure indicators in: {all_text[:100]}... Components: {len(components)}")
    return False

def needs_start_button(content, embeds):
    """Determine if message needs a start button"""
    all_text = content.lower()

    for embed in embeds:
        all_text += " " + str(embed.get("description", "")).lower()
        all_text += " " + str(embed.get("title", "")).lower()

    start_button_indicators = [
        "choose items",
        "bring along",
        "recommended",
        "adventure options",
        "select adventure",
        "pick items"
    ]

    return any(indicator in all_text for indicator in start_button_indicators)

def is_start_button(button):
    """Determine if button is a start button"""
    label = button["label"].lower().strip()
    custom_id = button["custom_id"].lower()

    start_indicators = [
        label == "start",
        label == "begin",
        label == "go",
        label == "start adventure",
        "start" in custom_id,
        "begin" in custom_id,
        custom_id.startswith("adventure-start:")
    ]

    return any(start_indicators)

# --- ENHANCED COOLDOWN DETECTION ---
def is_cooldown_message(content, embeds, components):
    """Determine if message contains cooldown information"""
    all_text = content.lower()

    for embed in embeds:
        all_text += " " + str(embed.get("title", "")).lower()
        all_text += " " + str(embed.get("description", "")).lower()
        if embed.get("fields"):
            for field in embed["fields"]:
                all_text += " " + str(field.get("name", "")).lower()
                all_text += " " + str(field.get("value", "")).lower()

    cooldown_keywords = [
        "adventure again in",
        "try again in",
        "cooldown",
        "wait",
        "minutes",
        "seconds",
        "hours"
    ]

    return any(keyword in all_text for keyword in cooldown_keywords) and not any(is_navigation_button(btn) for btn in extract_all_buttons(components))

def extract_cooldown_time(content, embeds, buttons=None):
    """Extract precise cooldown time from Discord message"""
    all_text = content.lower()

    for embed in embeds:
        all_text += " " + str(embed.get("title", "")).lower()
        all_text += " " + str(embed.get("description", "")).lower()
        if embed.get("fields"):
            for field in embed["fields"]:
                all_text += " " + str(field.get("name", "")).lower()
                all_text += " " + str(field.get("value", "")).lower()

    if buttons:
        for button in buttons:
            button_label = button.get("label", "").lower()
            if "adventure again in" in button_label and ("minute" in button_label or "hour" in button_label or "second" in button_label):
                all_text += " " + button_label
                print(f"🔘 Found cooldown in button label: '{button.get('label', '')}'")

    time_patterns = [
        (r"adventure again in (\d+)\s*minutes?", "minute"),
        (r"adventure again in (\d+)\s*mins?", "minute"),
        (r"adventure again in (\d+)\s*m\b", "minute"),
        (r"adventure again in (\d+)\s*minute", "minute"),
        (r"next adventure in (\d+)\s*minutes?", "minute"),
        (r"try again in (\d+)\s*minutes?", "minute"),
        (r"try again in (\d+)\s*mins?", "minute"),
        (r"wait (\d+)\s*minutes?", "minute"),
        (r"cooldown (\d+)\s*minutes?", "minute"),
        (r"adventure again in (\d+)\s*hours?", "hour"),
        (r"try again in (\d+)\s*hours?", "hour"),
        (r"adventure again in (\d+)\s*seconds?", "second"),
        (r"try again in (\d+)\s*seconds?", "second"),
    ]

    time_multipliers = {
        'hour': 3600,
        'minute': 60,
        'second': 1
    }

    for pattern, time_type in time_patterns:
        match = re.search(pattern, all_text)
        if match:
            time_value = int(match.group(1))
            seconds = time_value * time_multipliers[time_type]
            safety_buffer = random.randint(30, 90)
            total_seconds = seconds + safety_buffer
            print(f"⏰ COOLDOWN DETECTED: {time_value} {time_type}(s) + {safety_buffer}s buffer = {total_seconds}s total")
            return total_seconds

    default_with_buffer = ROUND_DELAY + random.randint(30, 80)
    print(f"⏰ DEFAULT COOLDOWN: {default_with_buffer}s (no match found)")
    return default_with_buffer

def is_truly_complete(content, embeds, buttons):
    """Enhanced check for adventure completion"""
    all_text = content.lower()

    for embed in embeds:
        all_text += " " + str(embed.get("title", "")).lower()
        all_text += " " + str(embed.get("description", "")).lower()
        if embed.get("fields"):
            for field in embed["fields"]:
                all_text += " " + str(field.get("name", "")).lower()
                all_text += " " + str(field.get("value", "")).lower()

    print(f"🔍 Checking completion with text: {all_text[:200]}...")

    for btn in buttons:
        if (btn.get("disabled", False) and 
            btn.get("label", "").lower().startswith("adventure again in") and
            "minute" in btn.get("label", "").lower()):
            print(f"🏆 COMPLETION: Disabled 'Adventure again in' button detected: '{btn['label']}'")
            return True

    true_completion_signs = [
        "adventure summary",
        "adventure again in",
        "your adventure is over",
        "adventure completed",
        "thanks for playing",
        "final results",
        "you lost all items",
        "summary"
    ]

    return any(sign in all_text for sign in true_completion_signs) and not any(is_navigation_button(btn) for btn in buttons)

# --- ENHANCED BUTTON DETECTION ---
def is_backpack_button(button):
    """Determine backpack buttons (red)"""
    emoji = button.get("emoji", {})
    style = button.get("style", 1)
    custom_id = button.get("custom_id", "").lower()
    label = button.get("label", "").lower()

    backpack_indicators = [
        emoji.get("name") == "🎒",
        "🎒" in str(button),
        style == 4,  # Red
        "inventory" in custom_id,
        "backpack" in label,
        "bag" in custom_id,
        custom_id.startswith("adventure-progress:") or custom_id.startswith("adventure-backpackitem:")
    ]

    is_backpack = any(backpack_indicators)
    if is_backpack:
        print(f"🎒 BACKPACK DETECTED: '{button['label']}' (style: {style}, custom_id: {custom_id})")

    return is_backpack

def is_navigation_button(button):
    """Determine navigation buttons (blue arrow or unlabeled)"""
    label = button["label"].strip()
    custom_id = button["custom_id"].lower()
    emoji = button.get("emoji", {})
    style = button.get("style", 1)

    navigation_indicators = [
        label == ">",
        label == "→",
        label == "▶",
        "next" in custom_id,
        "continue" in custom_id,
        "forward" in custom_id,
        style == 1 and label in [">", "→", "▶", "Continue", "Next"],
        custom_id.startswith("adventure-next:") or custom_id.startswith("adventure-continue:"),
        emoji.get("name") == "ArrowRightui" and emoji.get("id") == "1379166099895091251" and emoji.get("animated", False),
        not label and not button["disabled"] and not custom_id.startswith("adventure-backpackitem:")
    ]

    is_nav = any(navigation_indicators)
    if is_nav:
        print(f"🧭 NAVIGATION DETECTED: '{label}' (style: {style}, custom_id: {custom_id}, emoji: {emoji})")

    return is_nav

def needs_navigation_after_choice(content, embeds):
    """Determine if navigation is needed after choice"""
    all_text = content.lower()

    for embed in embeds:
        all_text += " " + str(embed.get("description", "")).lower()
        all_text += " " + str(embed.get("title", "")).lower()

    navigation_needed_signs = [
        "nothing interesting happened",
        "you passed a star",
        "you found",
        "you discovered",
        "you encountered",
        "turns out",
        "it seems",
        "you feel",
        "blob-like planet",
        "odd eyes",
        "kitchen"
    ]

    return any(sign in all_text for sign in navigation_needed_signs)

# --- SMART SCENARIO-BASED BUTTON SELECTION ---
def select_best_button(buttons, content, embeds, is_navigation_phase=False):
    """Smart button selection based on actual scenarios and choice memory"""
    if not buttons:
        return None

    # Filter disabled buttons
    enabled_buttons = [btn for btn in buttons if not btn["disabled"]]
    if not enabled_buttons:
        print("⚠️ All buttons are disabled")
        return None

    print(f"🎯 Selecting from {len(enabled_buttons)} enabled buttons:")
    for i, btn in enumerate(enabled_buttons):
        print(f"   {i+1}. '{btn['label']}' (ID: {btn['custom_id'][:30]}..., Style: {btn.get('style', 1)})")

    # PRIORITY 1: If in navigation phase, search for navigation button only
    if is_navigation_phase:
        for btn in enabled_buttons:
            if is_navigation_button(btn):
                print(f"🧭 NAVIGATION PHASE: Selected '{btn['label']}'")
                return btn
        print("❌ Navigation phase but no navigation button found")
        return None

    # PRIORITY 2: Navigation buttons always have highest priority (outside choice phase)
    navigation_buttons = [btn for btn in enabled_buttons if is_navigation_button(btn)]
    if navigation_buttons:
        selected = navigation_buttons[0]
        print(f"🧭 PRIORITY: Navigation button selected: '{selected['label']}'")
        return selected

    # PRIORITY 3: Avoid backpack buttons entirely
    non_backpack_buttons = [btn for btn in enabled_buttons if not is_backpack_button(btn)]

    if not non_backpack_buttons:
        print("⚠️ Only backpack buttons available - this shouldn't happen in normal gameplay")
        return None

    # PRIORITY 4: Check choice memory first
    scenario_key = create_scenario_key(content, embeds)
    remembered_choice = get_remembered_choice(scenario_key, non_backpack_buttons)

    if remembered_choice:
        print(f"🧠 Using remembered choice: '{remembered_choice['label']}'")
        return remembered_choice

    # Collect all text for analysis
    all_text = content.lower()
    for embed in embeds:
        all_text += " " + str(embed.get("description", "")).lower()
        all_text += " " + str(embed.get("title", "")).lower()

    print(f"📝 Analyzing new scenario: {all_text[:100]}...")

    # PRIORITY 5: Selection based on specific scenarios
    scenario_choices = {
        # Choose Items Phase
        "choose_items": {
            "keywords": ["choose items", "bring along", "recommended"],
            "best_choices": {
                "start": 15,
                "begin": 15,
                "go": 14
            },
            "avoid_choices": {
                "equip all": 2,
                "cancel": 1
            }
        },
        # Alien Encounters
        "alien": {
            "keywords": ["alien", "probe", "abduct", "space", "extraterrestrial"],
            "best_choices": {
                "talk": 10,
                "sit back": 10,
                "enjoy": 10,
                "cooperate": 9,
                "be friendly": 8,
                "do": 7,
                "try": 6
            },
            "avoid_choices": {
                "attack": 1,
                "fight": 2,
                "resist": 3,
                "probe": 4
            }
        },
        # Blob Planet
        "blob_planet": {
            "keywords": ["blob-like planet", "blob", "elusive blob", "grab one"],
            "best_choices": {
                "grab one": 10,
                "take": 9,
                "collect": 8,
                "inspect": 7
            },
            "avoid_choices": {
                "ignore": 2,
                "flee": 3
            }
        },
        # Dangerous Planets
        "dangerous_planet": {
            "keywords": ["toxic", "radioactive", "dangerous", "chemicals", "poison"],
            "best_choices": {
                "distant scan": 10,
                "scan": 9,
                "observe": 8,
                "avoid": 8,
                "leave": 7
            },
            "avoid_choices": {
                "land": 2,
                "explore": 3,
                "approach": 3
            }
        },
        # Kitchen/Food Scenarios with Angry Alien
        "kitchen_alien": {
            "keywords": ["kitchen", "food", "eat", "cook", "shady stuff", "angry alien"],
            "best_choices": {
                "flee": 15,
                "leave": 10,
                "run": 10
            },
            "avoid_choices": {
                "inspect": 2,
                "ignore": 3,
                "eat": 1,
                "approach": 1
            }
        },
        # Technical/Repair Scenarios
        "technical_repair": {
            "keywords": ["telescope", "repair", "fix", "broken", "technical"],
            "best_choices": {
                "try and fix": 10,
                "repair": 9,
                "fix": 9,
                "examine": 7
            },
            "avoid_choices": {
                "flee": 2,
                "ignore": 3,
                "destroy": 1
            }
        },
        # Space Objects
        "space_objects": {
            "keywords": ["star", "object", "strange", "floating", "shooting star"],
            "best_choices": {
                "reach for it": 10,
                "collect": 10,
                "inspect": 9,
                "wish": 8,
                "take picture": 7,
                "grab": 7
            },
            "avoid_choices": {
                "flee": 2,
                "ignore": 3,
                "avoid": 3
            }
        },
        # Fuel/Resource Management
        "fuel_resources": {
            "keywords": ["fuel", "ran out", "empty", "resource", "energy"],
            "best_choices": {
                "search planet": 10,
                "search": 9,
                "look for": 8,
                "find": 7
            },
            "avoid_choices": {
                "give up": 1,
                "urinate": 2
            }
        },
        # Communication/Transmission
        "communication": {
            "keywords": ["transmission", "signal", "communication", "message", "deep space"],
            "best_choices": {
                "respond": 10,
                "answer": 9,
                "investigate": 8,
                "decode": 8
            },
            "avoid_choices": {
                "ignore": 3
            }
        },
        # Odd Eyes Encounter
        "odd_eyes": {
            "keywords": ["odd eyes"],
            "best_choices": {
                "flee": 15
            },
            "avoid_choices": {
                "attack": 1,
                "fight": 1,
                "approach": 1,
                "inspect": 1
            }
        }
    }

    # Determine matching scenario
    matched_scenario = None
    scenario_name = None

    for name, scenario_data in scenario_choices.items():
        if any(keyword in all_text for keyword in scenario_data["keywords"]):
            matched_scenario = scenario_data
            scenario_name = name
            print(f"🎯 Matched scenario: {name}")
            break

    # Selection based on scenario
    if matched_scenario:
        button_scores = []

        for btn in non_backpack_buttons:
            label = btn["label"].lower().strip()
            score = 5  # Default score

            # Match with good choices
            for good_choice, points in matched_scenario["best_choices"].items():
                if good_choice in label or label in good_choice:
                    score = max(score, points)
                    print(f"✅ Good match: '{btn['label']}' -> {points} points")

            # Penalize for bad choices
            for bad_choice, penalty in matched_scenario["avoid_choices"].items():
                if bad_choice in label or label in bad_choice:
                    score = min(score, penalty)
                    print(f"❌ Bad match: '{btn['label']}' -> {penalty} points")

            button_scores.append((btn, score))

        # Sort by score
        button_scores.sort(key=lambda x: x[1], reverse=True)
        best_button = button_scores[0][0]
        best_score = button_scores[0][1]

        print(f"🏆 Best choice for {scenario_name}: '{best_button['label']}' ({best_score} points)")
        return best_button

    # PRIORITY 6: If no specific scenario, use general rules
    general_good_choices = ["help", "yes", "accept", "try", "start", "continue", "ok", "do", "inspect"]
    general_bad_choices = ["no", "refuse", "ignore", "give up"]

    for btn in non_backpack_buttons:
        label = btn["label"].lower()
        if any(good in label for good in general_good_choices):
            print(f"🔶 General good choice: '{btn['label']}'")
            return btn

    safe_buttons = []
    for btn in non_backpack_buttons:
        label = btn["label"].lower()
        if not any(bad in label for bad in general_bad_choices):
            safe_buttons.append(btn)

    if safe_buttons:
        selected = safe_buttons[0]
        print(f"🛡️ Safe choice: '{selected['label']}'")
        return selected

    selected = non_backpack_buttons[0]
    print(f"🎲 Default selection: '{selected['label']}'")
    return selected

# --- ENHANCED BUTTON CLICKING ---
def click_button(button, message_id, retry_count=0):
    """Click button with enhanced error handling"""
    global session_id

    if not all([button.get("custom_id"), message_id, session_id]):
        print(f"❌ Missing data for button click:")
        print(f"   Custom ID: {bool(button.get('custom_id'))}")
        print(f"   Message ID: {bool(message_id)}")
        print(f"   Session ID: {bool(session_id)}")
        return False

    url = f"{API_BASE}/interactions"

    payload = {
        "type": 3,
        "channel_id": str(CHANNEL_ID),
        "message_id": str(message_id),
        "application_id": str(DANK_MEMER_ID),
        "guild_id": str(GUILD_ID),
        "session_id": str(session_id),
        "data": {
            "component_type": 2,
            "custom_id": str(button["custom_id"])
        },
        "nonce": str(random.randint(100000000000000000, 999999999999999999))
    }

    print(f"🔘 Clicking button (attempt {retry_count + 1}):")
    print(f"   Label: '{button['label']}'")
    print(f"   Custom ID: {button['custom_id']}")
    print(f"   Message ID: {message_id}")

    try:
        response = requests.post(url, headers=HEADERS, json=payload, timeout=20)

        print(f"📡 Click response: {response.status_code}")

        if response.status_code in [200, 204]:
            print(f"✅ Successfully clicked: '{button['label']}'")
            with open(CHOICE_MEMORY_FILE + "_clicked_buttons.json", "a") as f:
                f.write(json.dumps({"custom_id": button["custom_id"], "label": button["label"], "timestamp": datetime.now().isoformat()}) + "\n")
            print(f"💾 Saved clicked button: {button['custom_id']}")
            return True
        elif response.status_code == 400:
            error_data = response.text
            print(f"❌ Bad Request: {error_data}")
            if retry_count < 2:
                print("🔄 Retrying with fresh message...")
                time.sleep(2)
                return try_fresh_click(button)
        elif response.status_code == 404:
            print(f"❌ Message not found")
            if retry_count < 2:
                return try_fresh_click(button)
        elif response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 10))
            print(f"⏰ Rate limited, waiting {retry_after}s")
            time.sleep(retry_after)
            if retry_count < 3:
                return click_button(button, message_id, retry_count + 1)
        else:
            print(f"❌ Unexpected response: {response.status_code} - {response.text}")

    except Exception as e:
        print(f"❌ Click error: {e}")
        if retry_count < 2:
            time.sleep(2)
            return click_button(button, message_id, retry_count + 1)

    return False

def try_fresh_click(original_button):
    """Try clicking with a fresh message"""
    try:
        url = f"{API_BASE}/channels/{CHANNEL_ID}/messages?limit=10"
        response = requests.get(url, headers=HEADERS, timeout=10)

        if response.status_code == 200:
            messages = response.json()

            for msg in messages:
                if (str(msg.get("author", {}).get("id")) == DANK_MEMER_ID and 
                    msg.get("components")):

                    fresh_buttons = extract_all_buttons(msg.get("components", []))

                    for btn in fresh_buttons:
                        if (btn["label"] == original_button["label"] or
                            btn["custom_id"].split(":")[0] == original_button["custom_id"].split(":")[0]):

                            if not btn["disabled"]:
                                print("🔄 Found matching button in fresh message")
                                return click_button(btn, msg["id"])

                    navigation_buttons = [b for b in fresh_buttons if is_navigation_button(b) and not b["disabled"]]
                    if navigation_buttons:
                        print("🔄 Using first available navigation button from fresh message")
                        return click_button(navigation_buttons[0], msg["id"])

    except Exception as e:
        print(f"❌ Fresh click failed: {e}")

    return False

# --- Message Sending Functions ---
def send_webhook(msg):
    if WEBHOOK_URL:
        try:
            requests.post(WEBHOOK_URL, json={"content": msg}, timeout=10)
        except Exception as e:
            print(f"Webhook error: {e}")

def send_message(content):
    global waiting_for_interaction, waiting_for_start_button, adventure_start_time, no_start_button_time

    url = f"{API_BASE}/channels/{CHANNEL_ID}/messages"
    payload = {"content": content}

    try:
        response = requests.post(url, headers=HEADERS, json=payload, timeout=15)
        if response.status_code == 200:
            message_data = response.json()
            message_id = message_data.get("id")
            print(f"✔️ Sent: {content} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
            send_webhook(f"🟢 Sent: `{content}` at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")

            if message_id and DELETE_MESSAGE_DELAY > 0:
                def delete_later():
                    time.sleep(DELETE_MESSAGE_DELAY)
                    try:
                        delete_url = f"{API_BASE}/channels/{CHANNEL_ID}/messages/{message_id}"
                        del_response = requests.delete(delete_url, headers=HEADERS, timeout=10)
                        if del_response.status_code == 204:
                            print(f"🗑️ Deleted: {content}")
                    except:
                        pass

                Thread(target=delete_later, daemon=True).start()

            if content in INTERACTIVE_COMMANDS:
                if remaining_cooldown <= 0:  # Only start if no cooldown
                    waiting_for_interaction = True
                    waiting_for_start_button = True
                    adventure_start_time = time.time()
                    no_start_button_time = time.time()
                    print(f"🎮 Started adventure interaction mode at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")

            return True
        else:
            print(f"❌ Failed to send: {response.status_code}")

    except Exception as e:
        print(f"❌ Send error at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}: {e}")

    return False

# --- MAIN MESSAGE HANDLER ---
def on_message(ws, message):
    global waiting_for_interaction, waiting_for_navigation, waiting_for_start_button
    global session_id, last_heartbeat, adventure_start_time, last_choice_time, no_start_button_time

    try:
        data = json.loads(message)
    except:
        print(f"❌ Failed to parse message at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}: {message[:100]}...")
        return

    # Handle heartbeat
    if data.get("op") == 1:
        ws.send(json.dumps({"op": 1, "d": data.get("d")}))
        last_heartbeat = time.time()
        return

    # Handle hello
    elif data.get("op") == 10:
        return

    # Handle ready
    elif data.get("t") == "READY":
        session_id = data["d"].get("session_id")
        print(f"💾 Session ID: {session_id} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
        return

    # Handle messages
    if data.get("t") in ["MESSAGE_CREATE", "MESSAGE_UPDATE"]:
        msg_data = data["d"]

        # ✅ CRITICAL: Only process messages from OUR channel and guild
        message_channel_id = str(msg_data.get("channel_id", ""))
        message_guild_id = str(msg_data.get("guild_id", ""))

        if message_channel_id != str(CHANNEL_ID):
            return

        if message_guild_id != str(GUILD_ID):
            return

        # Only Dank Memer messages
        if str(msg_data.get("author", {}).get("id")) != DANK_MEMER_ID:
            return

        print(f"\n{'='*60}")
        print(f"🤖 DANK MEMER MESSAGE - OUR CHANNEL at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
        print(f"{'='*60}")

        content = msg_data.get("content", "")
        embeds = msg_data.get("embeds", [])
        components = msg_data.get("components", [])  # Ensure components is defined here
        message_id = msg_data.get("id")

        print(f"📝 Content: {content[:150]}...")
        print(f"🖼️ Embeds: {len(embeds)}")
        print(f"🔧 Components: {len(components)}")
        print(f"🎮 Waiting for interaction: {waiting_for_interaction}")
        print(f"🧭 Waiting for navigation: {waiting_for_navigation}")
        print(f"🚀 Waiting for start button: {waiting_for_start_button}")

        # Extract buttons
        buttons = extract_all_buttons(components)

        # ✅ CRITICAL: Only process adventure-related messages
        if not is_adventure_message(content, embeds, components):
            print("🚫 Non-adventure message detected - ignoring")
            return

        # Check start button timeout
        if (waiting_for_start_button and no_start_button_time and 
            time.time() - no_start_button_time > NO_START_BUTTON_TIMEOUT):
            print(f"⏰ No start button timeout - sending another pls adv at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
            send_webhook("⏰ No start button found - retrying pls adv")
            waiting_for_start_button = False
            waiting_for_interaction = False
            no_start_button_time = None
            command_queue.put("pls adv")
            return

        # PRIORITY CHECK: Cooldown message detection
        if is_cooldown_message(content, embeds, components):
            global dynamic_round_delay, remaining_cooldown
            print(f"🕐 COOLDOWN MESSAGE DETECTED at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
            cooldown_time = extract_cooldown_time(content, embeds, buttons)
            dynamic_round_delay = cooldown_time
            remaining_cooldown = cooldown_time  # Set initial remaining cooldown

            duration = int(time.time() - adventure_start_time) if adventure_start_time else 0
            cooldown_minutes = cooldown_time // 60
            cooldown_seconds = cooldown_time % 60

            print(f"🏁 Adventure session ended!")
            print(f"   Duration: {duration}s")
            print(f"   Cooldown: {cooldown_minutes}m {cooldown_seconds}s")

            send_webhook(f"🏁 Adventure ended in {duration}s | Cooldown: {cooldown_minutes}m {cooldown_seconds}s")

            waiting_for_interaction = False
            waiting_for_navigation = False
            waiting_for_start_button = False
            adventure_start_time = None
            last_choice_time = None
            no_start_button_time = None
            return

        # Check adventure completion
        if is_truly_complete(content, embeds, buttons):
            print(f"🏁 Adventure completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
            duration = int(time.time() - adventure_start_time) if adventure_start_time else 0

            next_delay = extract_cooldown_time(content, embeds, buttons)
            dynamic_round_delay = next_delay
            remaining_cooldown = next_delay  # Set initial remaining cooldown

            send_webhook(f"🏁 Adventure completed in {duration}s - Next in {next_delay//60}min")
            waiting_for_interaction = False
            waiting_for_navigation = False
            waiting_for_start_button = False
            adventure_start_time = None
            last_choice_time = None
            no_start_button_time = None
            return

        if adventure_start_time and (time.time() - adventure_start_time > ADVENTURE_TIMEOUT):
            print(f"⏰ Adventure timeout ({ADVENTURE_TIMEOUT}s) - Retrying interaction at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
            send_webhook(f"⏰ Adventure timeout - Retrying pls adv")
            waiting_for_interaction = False
            waiting_for_navigation = False
            waiting_for_start_button = False
            adventure_start_time = None
            last_choice_time = None
            no_start_button_time = None
            command_queue.put("pls adv")  # Infinite retry
            return

        # Check start button first
        if waiting_for_start_button:
            start_buttons = [btn for btn in buttons if is_start_button(btn) and not btn["disabled"]]

            if start_buttons:
                print("🚀 Found start button!")
                selected_button = start_buttons[0]

                delay = random.uniform(2.0, 4.0)
                print(f"⏱️ Waiting {delay:.1f}s before clicking start...")
                time.sleep(delay)

                success = click_button(selected_button, message_id)

                if success:
                    send_webhook(f"🚀 Started: {selected_button['label']}")
                    waiting_for_start_button = False
                    no_start_button_time = None
                    print("✅ Adventure started!")
                else:
                    print("❌ Failed to click start button")

                return
            else:
                if needs_start_button(content, embeds):
                    print("🚀 Needs start button but none found - waiting...")
                    return
                else:
                    waiting_for_start_button = False
                    no_start_button_time = None

        # Determine if interaction is needed
        interaction_triggers = [
            "choose items", "recommended", "bring along",
            "what do you do", "approach", "encounter"
        ]

        needs_interaction = any(trigger in content.lower() for trigger in interaction_triggers)

        if needs_interaction and not waiting_for_interaction:
            waiting_for_interaction = True
            adventure_start_time = time.time()
            print("🎮 Adventure interaction started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
            send_webhook("🎮 Adventure interaction started")

        if not waiting_for_interaction:
            if needs_navigation_after_choice(content, embeds):
                navigation_buttons = [btn for btn in buttons if is_navigation_button(btn) and not btn["disabled"]]
                if navigation_buttons:
                    print("🧭 Found standalone navigation need")
                    selected_button = navigation_buttons[0]
                    delay = random.uniform(3.0, 5.0)
                    time.sleep(delay)
                    success = click_button(selected_button, message_id)
                    if success:
                        send_webhook(f"🧭 Standalone navigation: {selected_button['label']}")
                    return
            print("🔕 Not waiting for interaction")
            return

        if not buttons:
            print("⏳ No buttons found, waiting for next message...")
            return

        will_need_navigation = needs_navigation_after_choice(content, embeds)

        choice_buttons = []
        navigation_buttons = []
        backpack_buttons = []

        for btn in buttons:
            if btn["disabled"]:
                continue

            if is_backpack_button(btn):
                backpack_buttons.append(btn)
                print(f"🚫 BACKPACK: '{btn['label']}'")
            elif is_navigation_button(btn):
                navigation_buttons.append(btn)
                print(f"🧭 NAVIGATION: '{btn['label']}'")
            else:
                choice_buttons.append(btn)
                print(f"⚡ CHOICE: '{btn['label']}'")

        if navigation_buttons:
            selected_button = navigation_buttons[0]
            print(f"🧭 PRIORITY: Navigation button selected: '{selected_button['label']}'")
            delay = random.uniform(3.0, 5.0)
            time.sleep(delay)
            success = click_button(selected_button, message_id)
            if success:
                send_webhook(f"🧭 Navigation: {selected_button['label']}")
                waiting_for_navigation = will_need_navigation
                if will_need_navigation:
                    last_choice_time = time.time()
                    print(f"🧭 Entering navigation wait mode for {NAVIGATION_WAIT_TIME}s")
            return

        if choice_buttons:
            selected_button = select_best_button(choice_buttons, content, embeds, is_navigation_phase=False)

            if selected_button:
                scenario_key = create_scenario_key(content, embeds)
                delay = random.uniform(INTERACTION_MIN_DELAY, INTERACTION_MAX_DELAY)
                print(f"⏱️ Waiting {delay:.1f}s before clicking choice...")
                time.sleep(delay)
                success = click_button(selected_button, message_id)
                if success:
                    send_webhook(f"🔘 Choice: {selected_button['label']}")
                    remember_choice(scenario_key, selected_button['label'], True)
                    if will_need_navigation:
                        waiting_for_navigation = True
                        last_choice_time = time.time()
                        print(f"🧭 Entering navigation wait mode for {NAVIGATION_WAIT_TIME}s")
                else:
                    print("❌ Failed to click choice button")
                    remember_choice(scenario_key, selected_button['label'], False)
            else:
                print("❌ No suitable choice button found")
        else:
            print("❌ No suitable buttons found (all are backpack or disabled)")

        print(f"{'='*60}")

def on_open(ws):
    print(f"🌐 WebSocket opened at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
    identify = {
        "op": 2,
        "d": {
            "token": TOKEN,
            "intents": 33280,
            "properties": {
                "$os": "linux",
                "$browser": "chrome",
                "$device": "computer"
            }
        }
    }
    ws.send(json.dumps(identify))

def on_error(ws, error):
    print(f"❌ WebSocket error at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}: {error}")

def on_close(ws, code, msg):
    print(f"🔌 WebSocket closed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}: {code} - {msg}")
    time.sleep(5)
    run_websocket()

def run_websocket():
    global current_ws
    current_ws = WebSocketApp(
        "wss://gateway.discord.gg/?v=9&encoding=json",
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    def run_ws():
        current_ws.run_forever(ping_interval=20, ping_timeout=10)

    Thread(target=run_ws, daemon=True).start()
    return current_ws

# Connection monitoring
def monitor_connection():
    global current_ws, last_heartbeat
    while True:
        try:
            now = time.time()
            if current_ws is None or (now - last_heartbeat > 120):  # Reduced to 120 seconds for faster detection
                print(f"🔍 Connection issue detected, restarting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
                current_ws = run_websocket()
                last_heartbeat = now
            time.sleep(15)  # Reduced to 15 seconds for more frequent checks
        except Exception as e:
            print(f"🔍 Monitor error at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}: {e}")
            time.sleep(15)

# Command queue system
command_queue = queue.Queue()
stop_event = Event()

def command_worker():
    global waiting_for_interaction, waiting_for_navigation, waiting_for_start_button, adventure_start_time

    while not stop_event.is_set():
        try:
            command = command_queue.get(timeout=1)
            if command:
                print(f"🎯 Executing: {command} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
                if remaining_cooldown <= 0:  # Check cooldown before sending
                    success = send_message(command)
                    if success and command in INTERACTIVE_COMMANDS:
                        waited = 0
                        max_wait = ADVENTURE_TIMEOUT
                        while (waiting_for_interaction or waiting_for_navigation or waiting_for_start_button):
                            time.sleep(5)
                            waited += 5

                            if waited % 60 == 0:
                                status = "interaction" if waiting_for_interaction else ("navigation" if waiting_for_navigation else "start_button")
                                print(f"🕐 Adventure running ({status})... {waited}/{max_wait}s")

                            if waited >= max_wait:
                                print(f"⏰ Interaction timeout ({max_wait}s) - Retrying pls adv at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
                                send_webhook(f"⏰ Interaction timeout - Retrying pls adv")
                                waiting_for_interaction = False
                                waiting_for_navigation = False
                                waiting_for_start_button = False
                                adventure_start_time = None
                                command_queue.put("pls adv")  # Infinite retry
                                break

                        if not (waiting_for_interaction or waiting_for_navigation or waiting_for_start_button):
                            print(f"✅ Adventure completed in {waited}s at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
                else:
                    print(f"⏰ Cooldown active ({remaining_cooldown}s), skipping command")
                time.sleep(COMMAND_DELAY)
                command_queue.task_done()

        except queue.Empty:
            continue
        except Exception as e:
            print(f"❌ Command worker error at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}: {e}")

def start_adventure_farming():
    global current_ws, last_heartbeat, remaining_cooldown

    load_choice_memory()

    keep_alive()  # Start Flask server to keep Replit awake

    current_ws = run_websocket()

    Thread(target=monitor_connection, daemon=True).start()

    wait_count = 0
    while not session_id and wait_count < 60:
        print(f"⌛ Waiting for session_id at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}...")
        time.sleep(1)
        wait_count += 1

    if not session_id:
        print("❌ No session_id, restarting...")
        return start_adventure_farming()

    Thread(target=command_worker, daemon=True).start()

    last_heartbeat = time.time()
    count = 0

    while True:
        if os.path.exists(STOP_FILE):
            print("🛑 Stop file detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
            send_webhook("🛑 Bot stopped")
            stop_event.set()
            break

        count += 1
        print(f"\n🚀 Adventure Round #{count} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
        send_webhook(f"🚀 Round #{count} starting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")

        if remaining_cooldown > 0:
            print(f"⏳ Resuming cooldown: {remaining_cooldown} seconds remaining...")
            send_webhook(f"⏳ Resuming cooldown: {remaining_cooldown // 60}m {(remaining_cooldown % 60)}s remaining")
        else:
            command_queue.put("pls adv")
            command_queue.join()

        current_delay = max(remaining_cooldown, dynamic_round_delay + 120)
        delay_minutes = current_delay // 60
        delay_seconds = current_delay % 60

        if remaining_cooldown <= 0:
            print(f"⏳ Waiting {delay_minutes}m {delay_seconds}s for next round...")
            send_webhook(f"⏳ Next round in {delay_minutes}m {delay_seconds}s")
        remaining_cooldown = current_delay  # Reset remaining cooldown to full delay

        remaining = remaining_cooldown
        last_update = time.time()

        while remaining > 0:
            time.sleep(10)
            remaining -= 10
            remaining_cooldown = remaining  # Update remaining cooldown in real-time

            if remaining > 0 and int(time.time() - last_update) >= 60:
                remaining_minutes = remaining // 60
                remaining_seconds = remaining % 60

                if remaining_minutes > 0:
                    print(f"⏰ {remaining_minutes}m {remaining_seconds}s remaining...")
                    if remaining_minutes % 2 == 0:
                        send_webhook(f"⏰ {remaining_minutes}m remaining until next adventure")
                else:
                    print(f"⏰ {remaining_seconds}s remaining...")

                last_update = time.time()

            if os.path.exists(STOP_FILE):
                print("🛑 Stop file detected during wait")
                return

        remaining_cooldown = 0  # Reset remaining cooldown after wait
        save_choice_memory()

def main():
    restart_count = 0
    max_restarts = 15

    while restart_count < max_restarts:
        try:
            if not TOKEN or not CHANNEL_ID:
                print("❌ Missing TOKEN or CHANNEL_ID")
                sys.exit(1)

            keep_alive()
            print(f"🚀 Enhanced Adventure Bot Started (Attempt #{restart_count + 1}) at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
            print("🧠 Loaded choice memory system")
            print("🎲 Added random event detection and avoidance")
            print("🚀 Implemented start button detection and retry logic")
            print("🎯 Enhanced scenario-based decision making")
            print("🔧 Improved button detection and selection")
            print("🧭 Added smart navigation handling")
            print("🎒 Implemented backpack button avoidance")
            print("🌐 Added keep-alive with Flask server")
            send_webhook(f"✅ Enhanced Bot started (Attempt #{restart_count + 1}) with choice memory and keep-alive at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")

            start_adventure_farming()

        except KeyboardInterrupt:
            print("🛑 Stopped by user at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}")
            save_choice_memory()
            sys.exit(0)
        except Exception as e:
            restart_count += 1
            print(f"❌ Error (Attempt {restart_count}/{max_restarts}) at {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}: {e}")
            send_webhook(f"❌ Error (Attempt {restart_count}): {e}")
            save_choice_memory()

            if restart_count >= max_restarts:
                print("❌ Max restarts reached")
                sys.exit(1)

            wait_time = min(60 * restart_count, 300)
            print(f"🔄 Restarting in {wait_time}s...")
            time.sleep(wait_time)

if __name__ == "__main__":
    main()