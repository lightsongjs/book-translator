#!/bin/bash

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Error margin for ratio (0.7 - 1.3)
MIN_RATIO=0.7
MAX_RATIO=1.3

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  Sistem Automat de Traducere Capitole${NC}"
echo -e "${CYAN}========================================${NC}\n"

# Find the highest translated chapter number
echo -e "${BLUE}Caut ultimul capitol tradus...${NC}"
LAST_CHAPTER=$(ls -1 04_ro_chapters/*.md 2>/dev/null | grep -o 'CHAPTER_[0-9]*' | grep -o '[0-9]*' | sort -n | tail -1)

if [ -z "$LAST_CHAPTER" ]; then
    echo -e "${RED}Nu am găsit capitole traduse în 04_ro_chapters/${NC}"
    echo -e "${YELLOW}Începem cu capitolul 1${NC}"
    NEXT_CHAPTER=1
else
    NEXT_CHAPTER=$((LAST_CHAPTER + 1))
    echo -e "${GREEN}Ultimul capitol tradus: ${LAST_CHAPTER}${NC}"
fi

echo -e "${CYAN}Următorul capitol de tradus: ${NEXT_CHAPTER}${NC}\n"

# Open chapter for translation
echo -e "${BLUE}Deschid capitolul ${NEXT_CHAPTER} pentru traducere...${NC}"
.venv/bin/python book_translator.py --open-chapter "$NEXT_CHAPTER"

if [ $? -ne 0 ]; then
    echo -e "${RED}Eroare la deschiderea capitolului ${NEXT_CHAPTER}${NC}"
    exit 1
fi

# Wait for user to finish translation
echo -e "\n${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}Apasă orice tastă când ai terminat traducerea...${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
read -n 1 -s -r
echo ""

# Combine chapters
echo -e "\n${BLUE}Combin segmentele capitolului ${NEXT_CHAPTER}...${NC}"
.venv/bin/python book_translator.py --combine-chapter "$NEXT_CHAPTER"

if [ $? -ne 0 ]; then
    echo -e "${RED}Eroare la combinarea capitolului ${NEXT_CHAPTER}${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Capitolul combinat cu succes${NC}\n"

# Verify translation
echo -e "${BLUE}Verifică traducerea...${NC}"
VERIFY_OUTPUT=$(.venv/bin/python book_translator.py --verify "$NEXT_CHAPTER")
echo "$VERIFY_OUTPUT"

# Extract ratio from verify output
RATIO=$(echo "$VERIFY_OUTPUT" | grep "Ratio:" | awk '{print $2}')

if [ -z "$RATIO" ]; then
    echo -e "${RED}Nu am putut extrage ratio-ul din verificare${NC}"
    exit 1
fi

# Check if ratio is within acceptable range
RATIO_CHECK=$(awk -v ratio="$RATIO" -v min="$MIN_RATIO" -v max="$MAX_RATIO" 'BEGIN {
    if (ratio >= min && ratio <= max) print "OK"; else print "ERROR"
}')

echo ""
if [ "$RATIO_CHECK" = "OK" ]; then
    echo -e "${GREEN}✓ Ratio: ${RATIO} (în marjă de eroare: ${MIN_RATIO}-${MAX_RATIO})${NC}"
else
    echo -e "${RED}⚠ ATENȚIE: Ratio ${RATIO} în afara marjei de eroare (${MIN_RATIO}-${MAX_RATIO})${NC}"
    echo -e "${YELLOW}Te rog verifică traducerea manual!${NC}"
    read -p "Continui oricum? (y/n): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Procesul a fost anulat${NC}"
        exit 1
    fi
fi

# Combine all chapters into full book
echo ""
echo -e "${BLUE}Actualizez cartea completă cu toate capitolele...${NC}"
.venv/bin/python book_translator.py --combine-all-chapters

if [ $? -ne 0 ]; then
    echo -e "${RED}Eroare la combinarea cărții complete${NC}"
    exit 1
fi

echo ""

# Ask if user wants to send to Kindle
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
read -p "Trimitem capitolul ${NEXT_CHAPTER} pe Kindle? (y/n): " -n 1 -r
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [[ $REPLY =~ ^[Yy]$ ]]; then
    # Find the Romanian chapter file (case-insensitive)
    RO_CHAPTER_FILE=$(find 04_ro_chapters/ -iname "*chapter_${NEXT_CHAPTER}_ro.md" | head -1)

    if [ -z "$RO_CHAPTER_FILE" ]; then
        echo -e "${RED}Nu am găsit fișierul capitolului ${NEXT_CHAPTER}${NC}"
        exit 1
    fi

    echo -e "\n${BLUE}Trimit capitolul pe Kindle...${NC}"
    .venv/bin/python book_translator.py --sendtokindle "$RO_CHAPTER_FILE"

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Capitolul a fost trimis pe Kindle!${NC}"
    else
        echo -e "${RED}Eroare la trimiterea pe Kindle${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}Capitolul nu a fost trimis pe Kindle${NC}"
fi

echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✓ Gata! Capitolul ${NEXT_CHAPTER} a fost procesat${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
