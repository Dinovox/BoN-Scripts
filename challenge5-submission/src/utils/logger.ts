export enum LogLevel {
  DEBUG = 0,
  INFO = 1,
  WARN = 2,
  ERROR = 3,
}

export class Logger {
  private context: string;
  private minLevel: LogLevel;

  constructor(context: string, minLevel: LogLevel = LogLevel.INFO) {
    this.context = context;
    this.minLevel = minLevel;
  }

  private formatMessage(
    level: string,
    message: string,
    meta?: unknown,
  ): string {
    const timestamp = new Date().toISOString();
    let log = `[${timestamp}] [${level}] [${this.context}] ${message}`;
    if (meta) {
      log += ` ${JSON.stringify(meta)}`;
    }
    return log;
  }

  debug(message: string, meta?: unknown): void {
    if (this.minLevel <= LogLevel.DEBUG) {
      console.log(this.formatMessage('DEBUG', message, meta));
    }
  }

  info(message: string, meta?: unknown): void {
    if (this.minLevel <= LogLevel.INFO) {
      console.info(this.formatMessage('INFO', message, meta));
    }
  }

  warn(message: string, meta?: unknown): void {
    if (this.minLevel <= LogLevel.WARN) {
      console.warn(this.formatMessage('WARN', message, meta));
    }
  }

  error(message: string, error?: unknown): void {
    if (this.minLevel <= LogLevel.ERROR) {
      console.error(this.formatMessage('ERROR', message));
      if (error) {
        if (error instanceof Error) {
          console.error(error.stack);
        } else {
          console.error(error);
        }
      }
    }
  }
}
