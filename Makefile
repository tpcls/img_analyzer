CC ?= cc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -pedantic
LDFLAGS ?= -lm

BIN := clothing_analyzer
SRC := src/main.c src/clothing_analyzer.c

.PHONY: all clean

all: $(BIN)

$(BIN): $(SRC) src/clothing_analyzer.h
	$(CC) $(CFLAGS) -o $@ $(SRC) $(LDFLAGS)

clean:
	rm -f $(BIN)
