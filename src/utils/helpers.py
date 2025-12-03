# -*- coding: utf-8 -*-

def get_filename(symbol):
    """Получить безопасное имя файла для символа (с подчеркиваниями)"""
    return symbol.replace('/', '_')
