import random

# Grid size
ROWS = 3
COLS = 4

# Positions
START = (0, 0)
GOAL = (2, 3)

OBSTACLES = [
    (0, 2),
    (1, 1)
]

# Agent starts here
agent_pos = START


def display_grid():

    for r in range(ROWS):

        for c in range(COLS):

            pos = (r, c)

            if pos == agent_pos:
                print("A", end=" ")

            elif pos == GOAL:
                print("G", end=" ")

            elif pos in OBSTACLES:
                print("X", end=" ")

            else:
                print(".", end=" ")

        print()

    print("-" * 20)


def get_reward(position):

    if position == GOAL:
        return 100

    if position in OBSTACLES:
        return -50

    return -1


def move(action):

    global agent_pos

    r, c = agent_pos

    if action == "UP":
        r -= 1

    elif action == "DOWN":
        r += 1

    elif action == "LEFT":
        c -= 1

    elif action == "RIGHT":
        c += 1

    # Boundary check
    r = max(0, min(r, ROWS - 1))
    c = max(0, min(c, COLS - 1))

    agent_pos = (r, c)

    reward = get_reward(agent_pos)

    done = (
        agent_pos == GOAL
        or agent_pos in OBSTACLES
    )

    return reward, done


actions = [
    "UP",
    "DOWN",
    "LEFT",
    "RIGHT"
]

display_grid()

done = False
total_reward = 0

while not done:

    action = random.choice(actions)

    print("Action:", action)

    reward, done = move(action)

    total_reward += reward

    display_grid()

    print("Reward:", reward)
    print()

print("Episode Finished")
print("Total Reward:", total_reward)