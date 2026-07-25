import re
from typing import Optional, Dict

def parse_transaction_intent(text: str) -> Optional[Dict]:
    text = text.lower().strip()
    
    send_pattern = r'(?:send|transfer|pay|give)\s+([\d.]+)\s*([a-zA-Z]+)\s+(?:to|at)\s+(0x[a-fA-F0-9]{40})'
    send_match = re.search(send_pattern, text)
    if send_match:
        amount = float(send_match.group(1))
        token = send_match.group(2).upper()
        to_address = send_match.group(3)
        chain = 'polygon'
        if 'ethereum' in text or 'eth' in text:
            chain = 'ethereum'
        elif 'bsc' in text or 'bnb' in text:
            chain = 'bsc'
        elif 'arbitrum' in text or 'arb' in text:
            chain = 'arbitrum'
        elif 'base' in text:
            chain = 'base'
        if re.search(r'[13][a-km-zA-HJ-NP-Z1-9]{25,34}', text):
            chain = 'bitcoin'
            to_address = re.search(r'[13][a-km-zA-HJ-NP-Z1-9]{25,34}', text).group(0)
        return {
            "action": "send",
            "token": token,
            "amount": amount,
            "to_address": to_address,
            "chain": chain
        }
    
    stake_pattern = r'(?:stake|lock)\s+([\d.]+)\s*([a-zA-Z]+)'
    stake_match = re.search(stake_pattern, text)
    if stake_match:
        amount = float(stake_match.group(1))
        token = stake_match.group(2).upper()
        return {
            "action": "stake",
            "token": token,
            "amount": amount,
            "chain": 'polygon'
        }
    
    swap_pattern = r'(?:swap|exchange|convert)\s+([\d.]+)\s*([a-zA-Z]+)\s+(?:to|for)\s+([a-zA-Z]+)'
    swap_match = re.search(swap_pattern, text)
    if swap_match:
        amount = float(swap_match.group(1))
        from_token = swap_match.group(2).upper()
        to_token = swap_match.group(3).upper()
        return {
            "action": "swap",
            "from_token": from_token,
            "to_token": to_token,
            "amount": amount,
            "chain": 'polygon'
        }
    
    return None
