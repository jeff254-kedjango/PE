#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Colors for terminal output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}          WEESPAS DATABASE MASTER SETUP               ${NC}"
echo -e "${BLUE}======================================================${NC}"

# 1. Check if virtual environment is active
if [[ -z "$VIRTUAL_ENV" ]]; then
    echo -e "${RED}Warning: Virtual environment not detected.${NC}"
    echo -e "Please ensure your dependencies are installed."
fi

# 2. Database Reset (Optional - Uncomment if you want a fresh start every time)
# echo -e "${BLUE}Step 1: Dropping and Recreating Database...${NC}"
# dropdb weespas_db --if-exists
# createdb weespas_db

# 3. Run Alembic Migrations (If applicable)
# echo -e "${BLUE}Step 2: Applying Migrations...${NC}"
# alembic upgrade head

# 4. Sequential Seeding
echo -e "${BLUE}Step 3: Starting Seed Sequence...${NC}"

echo -e "\n${GREEN}[1/3] Running Core Seed...${NC}"
python3 seed.py

echo -e "\n${GREEN}[2/3] Running Expanded Kenyan Listings...${NC}"
python3 seed_expanded.py

echo -e "\n${GREEN}[3/3] Running Stats & Auth Finalization...${NC}"
python3 seed_stats.py

echo -e "\n${BLUE}======================================================${NC}"
echo -e "${GREEN}SUCCESS: Weespas test environment is ready!${NC}"
echo -e "Admin: admin@weespas.com / admin123"
echo -e "${BLUE}======================================================${NC}"