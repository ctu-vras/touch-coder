import log_setup
from app_info import PROGRAM_VERSION
from gui.resource_utils import get_app_dir


def main() -> None:
    app_dir = get_app_dir()
    log_setup.configure_logging(app_dir)
    log_setup.install_process_exception_hooks()

    # Import after the process hooks exist so import/startup failures are also
    # captured in the session log.
    from labeling_app import LabelingApp

    app = LabelingApp()
    log_setup.apply_config(app.config.log_level_console, app.config.log_keep_files)
    log_setup.install_tk_exception_hook(app)
    log_setup.log_session_header(
        version=PROGRAM_VERSION,
        config=app.config,
        app_dir=app_dir,
    )
    app.mainloop()
    log_setup.log_session_footer()


if __name__ == "__main__":
    main()
