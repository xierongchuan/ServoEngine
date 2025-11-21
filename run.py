#!/usr/bin/env python3
"""
Entry point for the OpenProducer Trading Bot.
"""
import sys
import os

# Add the current directory to sys.path to make 'src' importable
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.main import main

if __name__ == "__main__":
    main()
