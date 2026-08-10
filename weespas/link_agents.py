"""One-time script: create Agent profiles for users with role='agent' but no agent_id."""
import uuid
from PE.weespas.core.database import SessionLocal
from PE.weespas.models.user import User
from PE.weespas.models.property import Agent

def main():
    db = SessionLocal()
    try:
        unlinked = db.query(User).filter(
            User.role == "agent",
            User.agent_id.is_(None),
        ).all()

        if not unlinked:
            print("No unlinked agent users found.")
            return

        for user in unlinked:
            agent = Agent(
                id=str(uuid.uuid4()),
                agent_name=user.name,
                agent_phone_number=user.phone,
                email=user.email,
                is_verified=False,
                is_active=True,
            )
            db.add(agent)
            db.flush()
            user.agent_id = agent.id
            print(f"Linked {user.name} ({user.email}) -> agent {agent.id}")

        db.commit()
        print(f"\nDone. Created and linked {len(unlinked)} agent profile(s).")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    main()
