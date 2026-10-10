import subprocess
def get_recent_commits(limit=10):
    command=[
        "git","log",f"-{limit}","--pretty=format:%h","--name-only",
    ]
    result=subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout
def main():
    print("="*45)
    print("          IMPACTLENS")
    print("Repository Intelligence Engine")
    print("="*45)

    try:
        history=get_recent_commits()

        if not history.strip():
            print("\nNi commits found yet.")
            print("Add a commit or analyze another repository")
            return
        print("Recent Repository History:\n")
        print(history)

    except subprocess.CalledProcessError:
        print("\nGit could not read this repository.")
        print("Check that Git is initialized and has commits.")

    except FileNotFoundError:
        print("\nGit was not found.Check your Installations.")
if __name__=="__main__":
    main()                