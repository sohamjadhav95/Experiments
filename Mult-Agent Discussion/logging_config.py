import logging
import logging.handlers
import os

def setup_logging():
    log_dir = os.path.join(os.path.dirname(__file__), 'logs')
    os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    def _setup_logger(name, filename):
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        
        # Prevent log messages from propagating to the root logger to avoid duplicate console output
        logger.propagate = False

        # File Handler
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, filename), maxBytes=5*1024*1024, backupCount=3
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        
        # Console Handler
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(formatter)

        if not logger.handlers:
            logger.addHandler(fh)
            logger.addHandler(ch)
            
        return logger

    # Domain specific loggers
    app_logger = _setup_logger('app', 'app.log')
    api_logger = _setup_logger('api', 'api.log')
    debate_logger = _setup_logger('debate', 'debate.log')

    return app_logger, api_logger, debate_logger

app_logger, api_logger, debate_logger = setup_logging()
