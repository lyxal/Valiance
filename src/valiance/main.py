from __future__ import annotations


def main():
    while True:
        try:
            command = input("Enter a command: ")
            if command.lower() == "exit":
                print("Exiting the program.")
                break
            else:
                print(f"You entered: {command}")
        except KeyboardInterrupt:
            print("\nProgram interrupted. Exiting.")
            break


if __name__ == "__main__":
    main()
