# Procedural Game Narrative Generation

An AI-powered procedural RPG generator with dynamic narrative generation.

## Features

- **User Authentication**: Secure sign-up and login system
- **Procedural World Building**: AI-generated locations, characters, and quests
- **Dynamic Narrative**: Interactive storytelling powered by OpenAI
- **Character Management**: Track stats, inventory, skills, and relationships
- **Quest System**: Dynamic quest generation and tracking

## Setup

### Database Migration

If you have an existing database, run the migration to add the Users table:

```bash
mysql -u your_username -p your_database < database/migration_add_users.sql
```

For new installations, the Users table will be created automatically when the application starts.

### Environment Variables

Make sure to set a secure `SECRET_KEY` in your `.env` file:

```
SECRET_KEY=your-secure-random-secret-key-here
```

## Authentication

The application now requires users to create an account or sign in before accessing the main menu. User passwords are securely hashed using Werkzeug's password hashing utilities.

## **[Contact](https://github.com/ColeBallard/coleballard.github.io/blob/main/README.md)**
