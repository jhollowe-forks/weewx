#
#    Copyright (c) 2009-2023 Tom Keffer <tkeffer@gmail.com>
#
#    See the file LICENSE.txt for your full rights.
#
"""Configure databases used by WeeWX"""

# python imports
import datetime
import importlib
import logging
import optparse
import sys
import time

# weewx imports
import weecfg.database
import weedb
import weeutil.logger
import weewx.manager
import weewx.units
from weeutil.weeutil import timestamp_to_string, y_or_n, to_int

log = logging.getLogger(__name__)

usage = """%prog --help
       %prog --create
       %prog --reconfigure
       %prog --transfer --dest-binding=BINDING_NAME [--dry-run]
       %prog --add-column=NAME [--type=(REAL|INTEGER)]
       %prog --rename-column=NAME --to-name=NEW_NAME
       %prog --drop-columns=NAME1,NAME2,...
       %prog --check
       %prog --update [--dry-run]
       %prog --drop-daily
       %prog --rebuild-daily [--date=YYYY-mm-dd |
                                    [--from=YYYY-mm-dd] [--to=YYYY-mm-dd]]
                                    [--dry-run]
       %prog --reweight [--date=YYYY-mm-dd |
                               [--from=YYYY-mm-dd] [--to=YYYY-mm-dd]]
                               [--dry-run]
       %prog --calc-missing [--date=YYYY-mm-dd |
                                   [--from=YYYY-mm-dd[THH:MM]] [--to=YYYY-mm-dd[THH:MM]]]
       %prog --check-strings
       %prog --fix-strings [--dry-run]

Description:

Manipulate the WeeWX database. Most of these operations are handled
automatically by WeeWX, but they may be useful in special cases."""

epilog = """NOTE: MAKE A BACKUP OF YOUR DATABASE BEFORE USING THIS UTILITY!
Many of its actions are irreversible!"""


def main():
    # Create a command line parser:
    parser = optparse.OptionParser(usage=usage, epilog=epilog)

    # Add the various verbs...
    parser.add_option("--create", action='store_true',
                      help="Create the WeeWX database and initialize it with the"
                           " schema.")
    parser.add_option("--reconfigure", action='store_true',
                      help="Create a new database using configuration"
                           " information found in the configuration file."
                           " The new database will have the same name as the old"
                           " database, with a '_new' on the end.")
    parser.add_option("--transfer", action='store_true',
                      help="Transfer the WeeWX archive from source database "
                           "to destination database.")
    parser.add_option("--add-column", type=str, metavar="NAME",
                      help="Add new column NAME to database.")
    parser.add_option("--type", type=str, metavar="TYPE",
                      help="New database column type (INTEGER|REAL) "
                           "(option --add-column only).  Default is 'REAL'.")
    parser.add_option("--rename-column", type=str, metavar="NAME",
                      help="Rename the column with name NAME.")
    parser.add_option("--to-name", type=str, metavar="NEW_NAME",
                      help="New name of the column (option --rename-column only).")
    parser.add_option("--drop-columns", type=str, metavar="NAME1,NAME2,...",
                      help="Drop one or more columns. Names must be separated by commas, "
                           "with NO SPACES.")
    parser.add_option("--check", action="store_true",
                      help="Check the calculations in the daily summary tables.")
    parser.add_option("--update", action="store_true",
                      help="Update the daily summary tables if required and"
                           " recalculate the daily summary maximum windSpeed values.")
    parser.add_option("--calc-missing", dest="calc_missing", action="store_true",
                      help="Calculate and store any missing derived observations.")
    parser.add_option("--check-strings", action="store_true",
                      help="Check the archive table for null strings that may"
                           " have been introduced by a SQL editing program.")
    parser.add_option("--fix-strings", action='store_true',
                      help="Fix any null strings in a SQLite database.")
    parser.add_option("--drop-daily", action='store_true',
                      help="Drop the daily summary tables from a database.")
    parser.add_option("--rebuild-daily", action='store_true',
                      help="Rebuild the daily summaries from data in the archive table.")
    parser.add_option("--reweight", action="store_true",
                      help="Recalculate the weighted sums in the daily summaries.")

    # ... then add the various options:
    parser.add_option("--config", dest="config_path", type=str,
                      metavar="CONFIG_FILE",
                      help="Use configuration file CONFIG_FILE.")
    parser.add_option("--date", type=str, metavar="YYYY-mm-dd",
                      help="This date only (options --calc-missing and --rebuild-daily only).")
    parser.add_option("--from", dest="from_date", type=str, metavar="YYYY-mm-dd[THH:MM]",
                      help="Start with this date or date-time"
                           " (options --calc-missing and --rebuild-daily only).")
    parser.add_option("--to", dest="to_date", type=str, metavar="YYYY-mm-dd[THH:MM]",
                      help="End with this date or date-time"
                           " (options --calc-missing and --rebuild-daily only).")
    parser.add_option("--binding", metavar="BINDING_NAME", default='wx_binding',
                      help="The data binding to use. Default is 'wx_binding'.")
    parser.add_option("--dest-binding", metavar="BINDING_NAME",
                      help="The destination data binding (option --transfer only).")
    parser.add_option('--dry-run', action='store_true',
                      default=False,
                      help="Print what would happen but do not do it. Default is False.")

    # Now we are ready to parse the command line:
    options, args = parser.parse_args()

    if len(args) > 1:
        print("wee_database takes at most a single argument (the path to the configuration file).",
              file=sys.stderr)
        print("You have %d: %s." % (len(args), args), file=sys.stderr)
        sys.exit(2)

    # Do a check to see if the user used more than 1 'verb'
    if sum(x is not None for x in [options.create,
                                   options.reconfigure,
                                   options.transfer,
                                   options.add_column,
                                   options.rename_column,
                                   options.drop_columns,
                                   options.check,
                                   options.update,
                                   options.calc_missing,
                                   options.check_strings,
                                   options.fix_strings,
                                   options.drop_daily,
                                   options.rebuild_daily,
                                   options.reweight
                                   ]) != 1:
        sys.exit("Must specify one and only one verb.")

    # Check that the various options satisfy our rules

    if options.type and not options.add_column:
        sys.exit("Option --type can only be used with option --add-column")

    if options.to_name and not options.rename_column:
        sys.exit("Option --to-name can only be used with option --rename-column")

    if options.rename_column and not options.to_name:
        sys.exit("Option --rename-column requires option --to-name")

    if options.date and not (options.calc_missing or options.rebuild_daily
                             or options.reweight):
        sys.exit("Option --date can only be used with options "
                 "--calc-missing, --rebuild-daily, or --reweight")

    if options.from_date and not (options.calc_missing or options.rebuild_daily
                                  or options.reweight):
        sys.exit("Option --from can only be used with options "
                 "--calc-missing, --rebuild-daily, or --reweight")

    if options.to_date and not (options.calc_missing or options.rebuild_daily
                                or options.reweight):
        sys.exit("Option --to can only be used with options "
                 "--calc-missing, --rebuild-daily, or --reweight")

    if options.dest_binding and not options.transfer:
        sys.exit("Option --dest-binding can only be used with option --transfer")

    # get config_dict to use
    config_path, config_dict = weecfg.read_config(options.config_path, args)
    print("Using configuration file %s" % config_path)

    # Set weewx.debug as necessary:
    weewx.debug = to_int(config_dict.get('debug', 0))

    # Customize the logging with user settings.
    weeutil.logger.setup('wee_database', config_dict)

    # Add the 'user' package to PYTHONPATH
    weewx.add_user_path(config_dict)
    # Now we can import user.extensions
    importlib.import_module('user.extensions')

    db_binding = options.binding
    # Get the db name, be prepared to catch the error if the binding does not
    # exist
    try:
        database = config_dict['DataBindings'][db_binding]['database']
    except KeyError:
        # Couldn't find the database name, maybe the binding does not exist.
        # Notify the user and exit.
        print("Error obtaining database name from binding '%s'" % db_binding, file=sys.stderr)
        sys.exit("Perhaps you need to specify a different binding using --binding")
    else:
        print("Using database binding '%s', which is bound to database '%s'" % (db_binding,
                                                                                database))
    if options.dry_run and not (options.check or options.check_strings):
        print("This is a dry run. Nothing will actually be done.")

    if options.create:
        createMainDatabase(config_dict, db_binding, options.dry_run)

    elif options.reconfigure:
        reconfigMainDatabase(config_dict, db_binding, options.dry_run)

    elif options.transfer:
        transferDatabase(config_dict, db_binding, options)

    elif options.add_column:
        addColumn(config_dict, db_binding, options.add_column, options.type, options.dry_run)

    elif options.rename_column:
        renameColumn(config_dict, db_binding, options.rename_column, options.to_name,
                     options.dry_run)

    elif options.drop_columns:
        dropColumns(config_dict, db_binding, options.drop_columns, options.dry_run)

    elif options.check:
        check(config_dict, db_binding, options)

    elif options.update:
        update(config_dict, db_binding, options)

    elif options.calc_missing:
        calc_missing(config_dict, db_binding, options)

    elif options.check_strings:
        check_strings(config_dict, db_binding, options, fix=False)

    elif options.fix_strings:
        check_strings(config_dict, db_binding, options, fix=True)

    elif options.drop_daily:
        dropDaily(config_dict, db_binding, options.dry_run)

    elif options.rebuild_daily:
        rebuildDaily(config_dict, db_binding, options)

    elif options.reweight:
        reweight(config_dict, db_binding, options)

    if options.dry_run and not (options.check or options.check_strings):
        print("This was a dry run. Nothing was actually done.")


def createMainDatabase(config_dict, db_binding, dry_run=False):
    """Create the WeeWX database"""

    # Try a simple open. If it succeeds, that means the database
    # exists and is initialized. Otherwise, an exception will be thrown.
    try:
        with weewx.manager.open_manager_with_config(config_dict, db_binding) as dbmanager:
            print("Database '%s' already exists. Nothing done." % dbmanager.database_name)
    except weedb.OperationalError:
        if not dry_run:
            # Database does not exist. Try again, but allow initialization:
            with weewx.manager.open_manager_with_config(config_dict,
                                                        db_binding, initialize=True) as dbmanager:
                print("Created database '%s'" % dbmanager.database_name)


def dropDaily(config_dict, db_binding, dry_run):
    """Drop the daily summaries from a WeeWX database"""

    manager_dict = weewx.manager.get_manager_dict_from_config(config_dict, db_binding)
    database_name = manager_dict['database_dict']['database_name']

    print("Proceeding will delete all your daily summaries from database '%s'" % database_name)
    ans = y_or_n("Are you sure you want to proceed (y/n)? ")
    if ans == 'y':
        t1 = time.time()
        print("Dropping daily summary tables from '%s' ... " % database_name)
        try:
            with weewx.manager.open_manager_with_config(config_dict, db_binding) as dbmanager:
                try:
                    if not dry_run:
                        dbmanager.drop_daily()
                except weedb.OperationalError as e:
                    print("Error '%s'" % e, file=sys.stderr)
                    print("Drop daily summary tables failed for database '%s'" % database_name)
                else:
                    tdiff = time.time() - t1
                    print("Daily summary tables dropped from "
                          "database '%s' in %.2f seconds" % (database_name, tdiff))
        except weedb.OperationalError:
            # No daily summaries. Nothing to be done.
            print("No daily summaries found in database '%s'. Nothing done." % database_name)
    else:
        print("Nothing done.")


def rebuildDaily(config_dict, db_binding, options):
    """Rebuild the daily summaries."""

    manager_dict = weewx.manager.get_manager_dict_from_config(config_dict,
                                                              db_binding)
    database_name = manager_dict['database_dict']['database_name']

    # get the first and last good timestamps from the archive, these represent
    # our bounds for rebuilding
    with weewx.manager.Manager.open(manager_dict['database_dict']) as dbmanager:
        first_ts = dbmanager.firstGoodStamp()
        first_d = datetime.date.fromtimestamp(first_ts) if first_ts is not None else None
        last_ts = dbmanager.lastGoodStamp()
        last_d = datetime.date.fromtimestamp(last_ts) if first_ts is not None else None
    # determine the period over which we are rebuilding from any command line
    # date parameters
    from_dt, to_dt = _parse_dates(options)
    # we have start and stop datetime objects but we work on whole days only,
    # so need date object
    from_d = from_dt.date() if from_dt is not None else None
    to_d = to_dt.date() if to_dt is not None else None
    # advise the user/log what we will do
    if from_d is None and to_d is None:
        _msg = "All daily summaries will be rebuilt."
    elif from_d and not to_d:
        _msg = "Daily summaries from %s through the end (%s) will be rebuilt." % (from_d,
                                                                                  last_d)
    elif not from_d and to_d:
        _msg = "Daily summaries from the beginning (%s) through %s will be rebuilt." % (first_d,
                                                                                        to_d)
    elif from_d == to_d:
        _msg = "Daily summary for %s will be rebuilt." % from_d
    else:
        _msg = "Daily summaries from %s through %s inclusive will be rebuilt." % (from_d,
                                                                                  to_d)
    log.info(_msg)
    print(_msg)
    ans = y_or_n("Proceed (y/n)? ")
    if ans == 'n':
        log.info("Nothing done.")
        print("Nothing done.")
        return

    t1 = time.time()

    # Open up the database. This will create the tables necessary for the daily
    # summaries if they don't already exist:
    with weewx.manager.open_manager_with_config(config_dict,
                                                db_binding, initialize=True) as dbmanager:

        log.info("Rebuilding daily summaries in database '%s' ..." % database_name)
        print("Rebuilding daily summaries in database '%s' ..." % database_name)
        if options.dry_run:
            return
        else:
            # now do the actual rebuild
            nrecs, ndays = dbmanager.backfill_day_summary(start_d=from_d,
                                                          stop_d=to_d,
                                                          trans_days=20)
    tdiff = time.time() - t1
    # advise the user/log what we did
    log.info("Rebuild of daily summaries in database '%s' complete" % database_name)
    if nrecs:
        sys.stdout.flush()
        # fix a bit of formatting inconsistency if less than 1000 records
        # processed
        if nrecs >= 1000:
            print()
        if ndays == 1:
            _msg = "Processed %d records to rebuild 1 daily summary in %.2f seconds" % (nrecs,
                                                                                        tdiff)
        else:
            _msg = ("Processed %d records to rebuild %d daily summaries in %.2f seconds" % (nrecs,
                                                                                            ndays,
                                                                                            tdiff))
        print(_msg)
        print("Rebuild of daily summaries in database '%s' complete" % database_name)
    else:
        print("Daily summaries up to date in '%s'" % database_name)


def reweight(config_dict, db_binding, options):
    """Recalculate the weighted sums in the daily summaries."""

    # Determine the period over which we are rebuilding from any command line date parameters
    from_dt, to_dt = _parse_dates(options)
    # Convert from Datetime to Date objects
    from_d = from_dt.date() if from_dt is not None else None
    to_d = to_dt.date() if to_dt is not None else None

    # advise the user/log what we will do
    if from_d is None and to_d is None:
        msg = "The weighted sums in all the daily summaries will be recalculated."
    elif from_d and not to_d:
        msg = "The weighted sums in the daily summaries from %s through the end " \
              "will be recalculated." % from_d
    elif not from_d and to_d:
        msg = "The weighted sums in the daily summaries from the beginning through %s" \
              "will be recalculated." % to_d
    elif from_d == to_d:
        msg = "The weighted sums in the daily summary for %s will be recalculated." % from_d
    else:
        msg = "The weighted sums in the daily summaries from %s through %s, " \
              "inclusive, will be recalculated." % (from_d, to_d)

    log.info(msg)
    print(msg)
    ans = y_or_n("Proceed (y/n)? ")
    if ans == 'n':
        log.info("Nothing done.")
        print("Nothing done.")
        return

    t1 = time.time()

    # Open up the database.
    manager_dict = weewx.manager.get_manager_dict_from_config(config_dict, db_binding)
    database_name = manager_dict['database_dict']['database_name']
    with weewx.manager.open_manager_with_config(config_dict, db_binding) as dbmanager:

        log.info("Recalculating the weighted summaries in database '%s' ..." % database_name)
        print("Recalculating the weighted summaries in database '%s' ..." % database_name)
        if not options.dry_run:
            # Do the actual recalculations
            dbmanager.recalculate_weights(start_d=from_d, stop_d=to_d)

    msg = "Finished reweighting in %.1f seconds." % (time.time() - t1)
    log.info(msg)
    print(msg)


def reconfigMainDatabase(config_dict, db_binding, dry_run):
    """Create a new database, then populate it with the contents of an old database"""

    manager_dict = weewx.manager.get_manager_dict_from_config(config_dict,
                                                              db_binding)
    # Make a copy for the new database (we will be modifying it)
    new_database_dict = dict(manager_dict['database_dict'])

    # Now modify the database name
    new_database_dict['database_name'] = manager_dict['database_dict']['database_name'] + '_new'

    # First check and see if the new database already exists. If it does, check
    # with the user whether it's ok to delete it.
    try:
        if not dry_run:
            weedb.create(new_database_dict)
    except weedb.DatabaseExists:
        ans = y_or_n("New database '%s' already exists. "
                     "Delete it first (y/n)? " % new_database_dict['database_name'])
        if ans == 'y':
            weedb.drop(new_database_dict)
        else:
            print("Nothing done.")
            return

    # Get the unit system of the old archive:
    with weewx.manager.Manager.open(manager_dict['database_dict']) as old_dbmanager:
        old_unit_system = old_dbmanager.std_unit_system

    if old_unit_system is None:
        print("Old database has not been initialized. Nothing to be done.")
        return

    # Get the unit system of the new archive:
    try:
        target_unit_nickname = config_dict['StdConvert']['target_unit']
    except KeyError:
        target_unit_system = None
    else:
        target_unit_system = weewx.units.unit_constants[target_unit_nickname.upper()]

    print("Copying database '%s' to '%s'" % (manager_dict['database_dict']['database_name'],
                                             new_database_dict['database_name']))
    if target_unit_system is None or old_unit_system == target_unit_system:
        print("The new database will use the same unit system as the old ('%s')." %
              weewx.units.unit_nicknames[old_unit_system])
    else:
        print("Units will be converted from the '%s' system to the '%s' system." %
              (weewx.units.unit_nicknames[old_unit_system],
               weewx.units.unit_nicknames[target_unit_system]))

    ans = y_or_n("Are you sure you wish to proceed (y/n)? ")
    if ans == 'y':
        t1 = time.time()
        weewx.manager.reconfig(manager_dict['database_dict'],
                               new_database_dict,
                               new_unit_system=target_unit_system,
                               new_schema=manager_dict['schema'],
                               dry_run=dry_run)
        tdiff = time.time() - t1
        print("Database '%s' copied to '%s' in %.2f seconds."
              % (manager_dict['database_dict']['database_name'],
                 new_database_dict['database_name'],
                 tdiff))
    else:
        print("Nothing done.")


def transferDatabase(config_dict, db_binding, options):
    """Transfer 'archive' data from one database to another"""

    # do we have enough to go on, must have a dest binding
    if not options.dest_binding:
        print("Destination binding not specified. Nothing Done. Aborting.", file=sys.stderr)
        return
    # get manager dict for our source binding
    src_manager_dict = weewx.manager.get_manager_dict_from_config(config_dict,
                                                                  db_binding)
    # get manager dict for our dest binding
    try:
        dest_manager_dict = weewx.manager.get_manager_dict_from_config(config_dict,
                                                                       options.dest_binding)
    except weewx.UnknownBinding:
        # if we can't find the binding display a message then return
        print("Unknown destination binding '%s', confirm destination binding."
              % options.dest_binding, file=sys.stderr)
        print("Nothing Done. Aborting.", file=sys.stderr)
        return
    except weewx.UnknownDatabase:
        # if we can't find the database display a message then return
        print("Error accessing destination database, "
              "confirm destination binding and/or database.", file=sys.stderr)
        print("Nothing Done. Aborting.", file=sys.stderr)
        return
    except (ValueError, AttributeError):
        # maybe a schema issue
        print("Error accessing destination database.", file=sys.stderr)
        print("Maybe the destination schema is incorrectly specified "
              "in binding '%s' in weewx.conf?" % options.dest_binding, file=sys.stderr)
        print("Nothing Done. Aborting.", file=sys.stderr)
        return
    except weewx.UnknownDatabaseType:
        # maybe a [Databases] issue
        print("Error accessing destination database.", file=sys.stderr)
        print("Maybe the destination database is incorrectly defined in weewx.conf?",
              file=sys.stderr)
        print("Nothing Done. Aborting.", file=sys.stderr)
        return
    # get a manager for our source
    with weewx.manager.Manager.open(src_manager_dict['database_dict']) as src_manager:
        # How many source records?
        num_recs = src_manager.getSql("SELECT COUNT(dateTime) from %s;"
                                      % src_manager.table_name)[0]
        if not num_recs:
            # we have no source records to transfer so abort with a message
            print("No records found in source database '%s'."
                  % src_manager.database_name)
            print("Nothing done. Aborting.")
            return

        if not options.dry_run:  # is it a dry run ?
            # not a dry run, actually do the transfer
            ans = y_or_n("Transfer %s records from source database '%s' "
                         "to destination database '%s' (y/n)? "
                         % (num_recs, src_manager.database_name,
                            dest_manager_dict['database_dict']['database_name']))
            if ans == 'y':
                t1 = time.time()
                # wrap in a try..except in case we have an error
                try:
                    with weewx.manager.Manager.open_with_create(
                            dest_manager_dict['database_dict'],
                            table_name=dest_manager_dict['table_name'],
                            schema=dest_manager_dict['schema']) as dest_manager:
                        print("Transferring, this may take a while.... ")
                        sys.stdout.flush()

                        # This could generate a *lot* of log entries. Temporarily disable logging
                        # for events at or below INFO
                        logging.disable(logging.INFO)

                        # do the transfer, should be quick as it's done as a
                        # single transaction
                        nrecs = dest_manager.addRecord(src_manager.genBatchRecords(),
                                                       progress_fn=weewx.manager.show_progress)

                        # Remove the temporary restriction
                        logging.disable(logging.NOTSET)

                        tdiff = time.time() - t1
                        print("\nCompleted.")
                        if nrecs:
                            print("%s records transferred from source database '%s' to "
                                  "destination database '%s' in %.2f seconds."
                                  % (nrecs, src_manager.database_name,
                                     dest_manager.database_name, tdiff))
                        else:
                            print("Error. No records were transferred from source "
                                  "database '%s' to destination database '%s'."
                                  % (src_manager.database_name, dest_manager.database_name),
                                  file=sys.stderr)
                except ImportError:
                    # Probably when trying to load db driver
                    print("Error accessing destination database '%s'."
                          % (dest_manager_dict['database_dict']['database_name'],),
                          file=sys.stderr)
                    print("Nothing done. Aborting.", file=sys.stderr)
                    raise
                except (OSError, weedb.OperationalError):
                    # probably a weewx.conf typo or MySQL db not created
                    print("Error accessing destination database '%s'."
                          % dest_manager_dict['database_dict']['database_name'], file=sys.stderr)
                    print("Maybe it does not exist (MySQL) or is incorrectly "
                          "defined in weewx.conf?", file=sys.stderr)
                    print("Nothing done. Aborting.", file=sys.stderr)
                    return

            else:
                # we decided not to do the transfer
                print("Nothing done.")
                return
        else:
            # it's a dry run so say what we would have done then return
            print("Transfer %s records from source database '%s' "
                  "to destination database '%s'."
                  % (num_recs, src_manager.database_name,
                     dest_manager_dict['database_dict']['database_name']))


def addColumn(config_dict, db_binding, column_name, column_type, dry_run=False):
    """Add a single column to the database.
    column_name: The name of the new column.
    column_type: The type ("REAL"|"INTEGER|) of the new column.
    """
    column_type = column_type or 'REAL'
    ans = y_or_n(
        "Add new column '%s' of type '%s' to database (y/n)? " % (column_name, column_type))
    if ans == 'y':
        dbm = weewx.manager.open_manager_with_config(config_dict, db_binding)
        if not dry_run:
            dbm.add_column(column_name, column_type)
        print(f'New column {column_name} of type {column_type} added to database.')
    else:
        print("Nothing done.")


def renameColumn(config_dict, db_binding, old_column_name, new_column_name, dry_run=False):
    """Rename a column in the database. """
    ans = y_or_n("Rename column '%s' to '%s' (y/n)? " % (old_column_name, new_column_name))
    if ans == 'y':
        dbm = weewx.manager.open_manager_with_config(config_dict, db_binding)
        if not dry_run:
            dbm.rename_column(old_column_name, new_column_name)
        print("Column '%s' renamed to '%s'." % (old_column_name, new_column_name))
    else:
        print("Nothing done.")


def dropColumns(config_dict, db_binding, drop_columns, dry_run=False):
    """Drop a set of columns from the database"""
    drop_list = drop_columns.split(',')
    # In case the user ended the list of columns to be dropped with a comma, search for an
    # empty column name
    try:
        drop_list.remove('')
    except ValueError:
        pass
    ans = y_or_n("Drop column(s) '%s' from the database (y/n)? " % ", ".join(drop_list))
    if ans == 'y':
        drop_set = set(drop_list)
        dbm = weewx.manager.open_manager_with_config(config_dict, db_binding)
        # Now drop the columns. If one is missing, a NoColumnError will be raised. Be prepared
        # to catch it.
        try:
            print("This may take a while...")
            if not dry_run:
                dbm.drop_columns(drop_set)
        except weedb.NoColumnError as e:
            print(e, file=sys.stderr)
            print("Nothing done.")
        else:
            print("Column(s) '%s' dropped from the database" % ", ".join(drop_list))
    else:
        print("Nothing done.")


def check(config_dict, db_binding, options):
    """Check database and report outstanding fixes/issues.

    Performs the following checks:
    -   checks database version
    -   checks for null strings in SQLite database
    """

    t1 = time.time()

    # Check interval weighting
    print("Checking daily summary tables version...")

    # Get a database manager object
    dbm = weewx.manager.open_manager_with_config(config_dict, db_binding)

    # check the daily summary version
    _daily_summary_version = dbm._read_metadata('Version')
    msg = "Daily summary tables are at version %s" % _daily_summary_version
    log.info(msg)
    print(msg)

    if _daily_summary_version is not None and _daily_summary_version >= '2.0':
        # interval weighting fix has been applied
        msg = "Interval Weighting Fix is not required."
        log.info(msg)
        print(msg)
    else:
        print("Recommend running --update to recalculate interval weightings.")
    print("Daily summary tables version check completed in %0.2f seconds." % (time.time() - t1))

    # now check for null strings
    check_strings(config_dict, db_binding, options, fix=False)


def update(config_dict, db_binding, options):
    """Apply any required database fixes.

    Applies the following fixes:
    -   checks if database version is 3.0, if not interval weighting fix is
        applied
    -   recalculates windSpeed daily summary max and maxtime fields from
        archive
    """

    # prompt for confirmation if it is not a dry run
    ans = 'y' if options.dry_run else None
    while ans not in ['y', 'n']:
        ans = input("The update process does not affect archive data, "
                    "but does alter the database.\nContinue (y/n)? ")
    if ans == 'n':
        # we decided not to update the summary tables
        msg = "Update cancelled"
        log.info(msg)
        print(msg)
        return

    log.info("Preparing interval weighting fix...")
    print("Preparing interval weighting fix...")

    # Get a database manager object
    dbm = weewx.manager.open_manager_with_config(config_dict, db_binding)

    # check the daily summary version
    msg = "Daily summary tables are at version %s" % dbm.version
    log.info(msg)
    print(msg)

    if dbm.version is not None and dbm.version >= '4.0':
        # interval weighting fix has been applied
        msg = "Interval weighting fix is not required."
        log.info(msg)
        print(msg)
    else:
        # apply the interval weighting
        msg = "Calculating interval weights..."
        log.info(msg)
        print(msg)
        t1 = time.time()
        if not options.dry_run:
            dbm.update()
        msg = "Interval Weighting Fix completed in %0.2f seconds." % (time.time() - t1)
        log.info(msg)
        print(msg)

    # recalc the max/maxtime windSpeed values
    _fix_wind(config_dict, db_binding, options)


def calc_missing(config_dict, db_binding, options):
    """Calculate any missing derived observations and save to database."""

    msg = "Preparing to calculate missing derived observations..."
    log.info(msg)

    # get a db manager dict given the config dict and binding
    manager_dict = weewx.manager.get_manager_dict_from_config(config_dict,
                                                              db_binding)
    # Get the table_name used by the binding, it could be different to the
    # default 'archive'. If for some reason it is not specified then fail hard.
    table_name = manager_dict['table_name']
    # get the first and last good timestamps from the archive, these represent
    # our overall bounds for calculating missing derived obs
    with weewx.manager.Manager.open(manager_dict['database_dict'],
                                    table_name=table_name) as dbmanager:
        first_ts = dbmanager.firstGoodStamp()
        last_ts = dbmanager.lastGoodStamp()
    # process any command line options that may limit the period over which
    # missing derived obs are calculated
    start_dt, stop_dt = _parse_dates(options)
    # we now have a start and stop date for processing, we need to obtain those
    # as epoch timestamps, if we have no start and/or stop date then use the
    # first or last good timestamp instead
    start_ts = time.mktime(start_dt.timetuple()) if start_dt is not None else first_ts - 1
    stop_ts = time.mktime(stop_dt.timetuple()) if stop_dt is not None else last_ts
    # notify if this is a dry run
    if options.dry_run:
        msg = "This is a dry run, missing derived observations will be calculated but not saved"
        log.info(msg)
        print(msg)
    _head = "Missing derived observations will be calculated "
    # advise the user/log what we will do
    if start_dt is None and stop_dt is None:
        _tail = "for all records."
    elif start_dt and not stop_dt:
        _tail = "from %s through to the end (%s)." % (timestamp_to_string(start_ts),
                                                      timestamp_to_string(stop_ts))
    elif not start_dt and stop_dt:
        _tail = "from the beginning (%s) through to %s." % (timestamp_to_string(start_ts),
                                                            timestamp_to_string(stop_ts))
    else:
        _tail = "from %s through to %s inclusive." % (timestamp_to_string(start_ts),
                                                      timestamp_to_string(stop_ts))
    _msg = "%s%s" % (_head, _tail)
    log.info(_msg)
    print(_msg)
    ans = y_or_n("Proceed (y/n)? ")
    if ans == 'n':
        _msg = "Nothing done."
        log.info(_msg)
        print(_msg)
        return

    t1 = time.time()

    # construct a CalcMissing config dict
    calc_missing_config_dict = {'name': 'Calculate Missing Derived Observations',
                                'binding': db_binding,
                                'start_ts': start_ts,
                                'stop_ts': stop_ts,
                                'trans_days': 20,
                                'dry_run': options.dry_run}

    # obtain a CalcMissing object
    calc_missing_obj = weecfg.database.CalcMissing(config_dict,
                                                   calc_missing_config_dict)
    log.info("Calculating missing derived observations...")
    print("Calculating missing derived observations...")
    # Calculate and store any missing observations. Be prepared to
    # catch any exceptions from CalcMissing.
    try:
        calc_missing_obj.run()
    except weewx.UnknownBinding as e:
        # We have an unknown binding, this could occur if we are using a
        # non-default binding and StdWXCalculate has not been told (via
        # weewx.conf) to use the same binding. Log it and notify the user then
        # exit.
        _msg = "Error: '%s'" % e
        print(_msg)
        log.error(_msg)
        print("Perhaps StdWXCalculate is using a different binding. Check "
              "configuration file [StdWXCalculate] stanza")
        sys.exit("Nothing done. Aborting.")
    else:
        msg = "Missing derived observations calculated in %0.2f seconds" % (time.time() - t1)
        log.info(msg)
        print(msg)


def _fix_wind(config_dict, db_binding, options):
    """Recalculate the windSpeed daily summary max and maxtime fields.

    Create a WindSpeedRecalculation object and call its run() method to
    recalculate the max and maxtime fields from archive data. This process is
    idempotent so it can be called repeatedly with no ill effect.
    """

    t1 = time.time()
    msg = "Preparing maximum windSpeed fix..."
    log.info(msg)
    print(msg)

    # notify if this is a dry run
    if options.dry_run:
        print("This is a dry run: maximum windSpeed will be calculated but not saved.")

    # construct a windSpeed recalculation config dict
    wind_config_dict = {'name': 'Maximum windSpeed fix',
                        'binding': db_binding,
                        'trans_days': 100,
                        'dry_run': options.dry_run}

    # create a windSpeedRecalculation object
    wind_obj = weecfg.database.WindSpeedRecalculation(config_dict,
                                                      wind_config_dict)
    # perform the recalculation, wrap in a try..except to catch any db errors
    try:
        wind_obj.run()
    except weedb.NoTableError:
        msg = "Maximum windSpeed fix applied: no windSpeed found"
        log.info(msg)
        print(msg)
    else:
        msg = "Maximum windSpeed fix completed in %0.2f seconds" % (time.time() - t1)
        log.info(msg)
        print(msg)


# These functions are necessary because Python 3 does not allow you to
# parameterize types. So, we use a big if-else.

def check_type(val, expected):
    if expected == 'INTEGER':
        return isinstance(val, int)
    elif expected == 'REAL':
        return isinstance(val, float)
    elif expected == 'STR' or expected == 'TEXT':
        return isinstance(val, str)
    else:
        raise ValueError("Unknown type %s" % expected)


def set_type(val, target):
    if target == 'INTEGER':
        return int(val)
    elif target == 'REAL':
        return float(val)
    elif target == 'STR' or target == 'TEXT':
        if isinstance(val, bytes):
            return val.decode('utf-8')
        return str(val)
    else:
        raise ValueError("Unknown type %s" % target)


def check_strings(config_dict, db_binding, options, fix=False):
    """Scan the archive table for null strings.

    Identifies and lists any null string occurrences in the archive table. If
    fix is True then any null strings that are found are fixed.
    """

    t1 = time.time()
    if options.dry_run or not fix:
        logging.disable(logging.INFO)

    print("Preparing Null String Fix, this may take a while...")

    if fix:
        log.info("Preparing Null String Fix")
        # notify if this is a dry run
        if options.dry_run:
            print("This is a dry run: null strings will be detected but not fixed")

    # open up the main database archive table
    with weewx.manager.open_manager_with_config(config_dict, db_binding) as dbmanager:

        obs_list = []
        obs_type_list = []

        # get the schema and extract the Python type each observation type should be
        for column in dbmanager.connection.genSchemaOf('archive'):
            # Save the observation name for this column (eg, 'outTemp'):
            obs_list.append(column[1])
            # And its type
            obs_type_list.append(column[2])

        records = 0
        _found = []
        # cycle through each row in the database
        for record in dbmanager.genBatchRows():
            records += 1
            # now examine each column
            for icol in range(len(record)):
                # check to see if this column is an instance of the correct
                # Python type
                if record[icol] is not None and not check_type(record[icol], obs_type_list[icol]):
                    # Oops. Found a bad one. Print it out.
                    if fix:
                        log.info("Timestamp = %s; record['%s']= %r; ... "
                                 % (record[0], obs_list[icol], record[icol]))

                    if fix:
                        # coerce to the correct type. If it can't be done, then
                        # set it to None.
                        try:
                            corrected_value = set_type(record[icol], obs_type_list[icol])
                        except ValueError:
                            corrected_value = None
                        # update the database with the new value but only if
                        # it's not a dry run
                        if not options.dry_run:
                            dbmanager.updateValue(record[0], obs_list[icol], corrected_value)
                        _found.append((record[0], obs_list[icol], record[icol], corrected_value))
                        # log it
                        log.info("     changed to %r\n" % corrected_value)
                    else:
                        _found.append((record[0], obs_list[icol], record[icol]))
            # notify the user of progress
            if records % 1000 == 0:
                print("Checking record: %d; Timestamp: %s\r"
                      % (records, timestamp_to_string(record[0])), end=' ')
                sys.stdout.flush()
    # update our final count now that we have finished
    if records:
        print("Checking record: %d; Timestamp: %s\r"
              % (records, timestamp_to_string(record[0])), end=' ')
        print()
    else:
        print("No records in database.")
    tdiff = time.time() - t1
    # now display details of what we found if we found any null strings
    if len(_found):
        print("The following null strings were found:")
        for item in _found:
            if len(item) == 4:
                print("Timestamp = %s; record['%s'] = %r; ... changed to %r" % item)
            else:
                print("Timestamp = %s; record['%s'] = %r; ... ignored" % item)
    # how many did we fix?
    fixed = len([a for a in _found if len(a) == 4])
    # summarise our results
    if len(_found) == 0:
        # found no null strings, log it and display on screen
        log.info("No null strings found.")
        print("No null strings found.")
    elif fixed == len(_found):
        # fixed all we found
        if options.dry_run:
            # its a dry run so display to screen but not to log
            print("%d of %d null strings found would have been fixed." % (fixed, len(_found)))
        else:
            # really did fix so log and display to screen
            log.info("%d of %d null strings found were fixed." % (fixed, len(_found)))
            print("%d of %d null strings found were fixed." % (fixed, len(_found)))
    elif fix:
        # this should never occur - found some but didn't fix them all when we
        # should have
        if options.dry_run:
            # its a dry run so say what would have happened
            print("Could not fix all null strings. "
                  "%d of %d null strings found would have been fixed." % (fixed,
                                                                          len(_found)))
        else:
            # really did fix so log and display to screen
            log.info("Could not fix all null strings. "
                     "%d of %d null strings found were fixed." % (fixed,
                                                                  len(_found)))
            print("Could not fix all null strings. "
                  "%d of %d null strings found were fixed." % (fixed,
                                                               len(_found)))
    else:
        # found some null string but it was only a check not a fix, just
        # display to screen
        print("%d null strings were found.\r\n"
              "Recommend running --fix-strings to fix these strings." % len(_found))

    # and finally details on time taken
    if fix:
        log.info("Applied Null String Fix in %0.2f seconds." % tdiff)
        print("Applied Null String Fix in %0.2f seconds." % tdiff)
    else:
        # it was a check not a fix so just display to screen
        print("Completed Null String Check in %0.2f seconds." % tdiff)
    # just in case, set the syslog level back where we found it
    if options.dry_run or not fix:
        logging.disable(logging.NOTSET)


def _parse_dates(options):
    """Parse --date, --from and --to command line options.

        Parses --date or --from and --to to determine a date-time span to be
        used. --to and --from in the format y-m-dTHH:MM precisely define a
        date-time but anything in the format y-m-d does not. When rebuilding
        the daily summaries this imprecision is not import as we merely need a
        date-time somewhere within the day being rebuilt. When calculating
        missing fields we need date-times for the span over which the
        calculations are to be performed.

        Inputs:
            options: the optparse options

        Returns: A two-way tuple (from_dt, to_dt) representing the from and to
        date-times derived from the --date or --to and --from command line
        options where
            from_dt: A datetime.datetime object holding the from date-time. May
                     be None
            to_dt:   A datetime.datetime object holding the to date-time. May be
                     None
    """

    # default is None, unless user has specified an option
    _from_dt = None
    _to_dt = None

    # first look for --date
    if options.date:
        # we have a --date option, make sure we are not over specified
        if options.from_date or options.to_date:
            raise ValueError("Specify either --date or a --from and --to combination; not both")

        # there is a --date but is it valid
        try:
            # this will give a datetime object representing midnight at the
            # start of the day
            _from_dt = datetime.datetime.strptime(options.date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Invalid --date option specified.")
        else:
            # we have the from date-time, for a --date option our final results
            # depend on the action we are to perform
            if options.rebuild_daily or options.reweight:
                # The daily summaries are stamped with the midnight timestamp
                # for each day, so our from and to results need to be within the
                # same calendar day else we will rebuild more than just one day.
                # For simplicity make them the same.
                _to_dt = _from_dt
            elif options.calc_missing:
                # On the other hand calc missing will be dealing with archive
                # records which are epoch timestamped. The midnight stamped
                # record is part of the previous day so make our from result
                # one second after midnight. THe to result must be midnight at
                # the end of the day.
                _to_dt = _from_dt + datetime.timedelta(days=1)
                _from_dt = _from_dt + datetime.timedelta(seconds=1)
            else:
                # nothing else uses from and to (yet) but just in case return
                # midnight to midnight as the default
                _to_dt = _from_dt + datetime.timedelta(days=1)
            # we have our results so we can return
            return _from_dt, _to_dt

    # we don't have --date so now look for --from and --to
    if options.from_date:
        # we have a --from but is it valid
        try:
            if 'T' in options.from_date:
                # we have a time so we can precisely determine a date-time
                _from_dt = datetime.datetime.strptime(options.from_date, "%Y-%m-%dT%H:%M")
            else:
                # we have a date only, so use midnight at the start of the day
                _from_dt = datetime.datetime.strptime(options.from_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Invalid --from option specified.")

    if options.to_date:
        # we have a --to but is it valid
        try:
            if 'T' in options.to_date:
                # we have a time so decode and use that
                _to_dt = datetime.datetime.strptime(options.to_date, "%Y-%m-%dT%H:%M")
            else:
                # we have a date, first obtain a datetime object for midnight
                # at the start of the day specified
                _to_dt = datetime.datetime.strptime(options.to_date, "%Y-%m-%d")
                # since we have a date the result we want depends on what action
                # we are to complete
                if options.rebuild_daily or options.reweight:
                    # for a rebuild the to date-time must be within the date
                    # specified date, which it already is so leave it
                    pass
                elif options.calc_missing:
                    # for calc missing we want midnight at the end of the day
                    _to_dt = _to_dt + datetime.timedelta(days=1)
                else:
                    # nothing else uses from and to (yet) but just in case
                    # return midnight at the end of the day
                    _to_dt = _to_dt + datetime.timedelta(days=1)
        except ValueError:
            raise ValueError("Invalid --to option specified.")

    # if we have both from and to date-times make sure from is no later than to
    if _from_dt and _to_dt and _to_dt < _from_dt:
        raise weewx.ViolatedPrecondition("--from value is later than --to value.")
    # we have our results so we can return
    return _from_dt, _to_dt


if __name__ == "__main__":
    main()
