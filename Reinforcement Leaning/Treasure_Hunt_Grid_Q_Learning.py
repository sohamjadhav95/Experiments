import random

ROWS = 3
COLS = 4

START = (0, 0)
GOAL = (2, 3)

OBSTACLES = [
    (0, 2),
    (1, 1)
]

ACTIONS = [
    "UP",
    "DOWN",
    "LEFT",
    "RIGHT"
]

ALPHA = 0.1      # learning rate
GAMMA = 0.9      # future importance
EPSILON = 0.2    # exploration

# -------------------
# Q TABLE
# -------------------

Q = {}

for r in range(ROWS):
    for c in range(COLS):
        for action in ACTIONS:
            Q[((r, c), action)] = 0

# -------------------
# ENVIRONMENT
# -------------------

def get_reward(state):

    if state == GOAL:
        return 100

    if state in OBSTACLES:
        return -50

    return -1


def move(state, action):

    r, c = state

    if action == "UP":
        r -= 1

    elif action == "DOWN":
        r += 1

    elif action == "LEFT":
        c -= 1

    elif action == "RIGHT":
        c += 1

    r = max(0, min(r, ROWS - 1))
    c = max(0, min(c, COLS - 1))

    next_state = (r, c)

    reward = get_reward(next_state)

    done = (
        next_state == GOAL
        or next_state in OBSTACLES
    )

    return next_state, reward, done


# -------------------
# ACTION SELECTION
# -------------------

def choose_action(state):

    if random.random() < EPSILON:
        return random.choice(ACTIONS)

    return max(
        ACTIONS,
        key=lambda a: Q[(state, a)]
    )


# -------------------
# TRAINING
# -------------------

success_count = 0

EPISODES = 100

for episode in range(1, EPISODES + 1):

    state = START

    total_reward = 0

    path = [state]

    done = False

    while not done:

        action = choose_action(state)

        next_state, reward, done = move(state, action)

        # Q-learning update

        best_future = max(
            Q[(next_state, a)]
            for a in ACTIONS
        )

        old_q = Q[(state, action)]

        Q[(state, action)] = old_q + ALPHA * (
            reward +
            GAMMA * best_future -
            old_q
        )

        state = next_state

        path.append(state)

        total_reward += reward

    if state == GOAL:
        success_count += 1

    # Print every 10 episodes

    if episode % 10 == 0:

        print("\n" + "=" * 50)

        print(f"Episode {episode}")

        print("\nPath:")
        print(" -> ".join(map(str, path)))

        print("\nReward:", total_reward)

        print(
            "Success Rate:",
            f"{success_count}/{episode}"
        )

        print("\nGuidebook Snapshot")

        important_states = [
            (0, 0),
            (1, 0),
            (2, 1),
            (2, 2)
        ]

        for s in important_states:

            print(f"\nState {s}")

            for a in ACTIONS:

                print(
                    f"{a:6}",
                    round(Q[(s, a)], 2)
                )