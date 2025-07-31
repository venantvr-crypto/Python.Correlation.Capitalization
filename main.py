import uuid

from crypto_analyzer import CryptoAnalyzer

if __name__ == "__main__":
    analyzer = CryptoAnalyzer(weeks=50, top_n_coins=5000, correlation_threshold=0.8, rsi_period=14,
                              session_guid=str(uuid.uuid4()))
    analyzer.run()
