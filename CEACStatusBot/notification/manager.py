import json
import os
import datetime

import pytz
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from CEACStatusBot.captcha import CaptchaHandle, OnnxCaptchaHandle
from CEACStatusBot.request import query_status

from .handle import NotificationHandle
import requests
from bs4 import BeautifulSoup
from tabulate import tabulate
from typing import Dict, Tuple
from datetime import datetime, timedelta
DEFAULT_ACTIVE_HOURS = "00:00-23:59"


class NotificationManager:
    def __init__(
            self,
            location: str,
            number: str,
            passport_number: str,
            surname: str,
            captchaHandle: CaptchaHandle = OnnxCaptchaHandle("captcha.onnx"),
    ) -> None:
        self.__handleList = []
        self.__location = location
        self.__number = number
        self.__captchaHandle = captchaHandle
        self.__passport_number = passport_number
        self.__surname = surname
        self.__status_file = "status_record.json"

    def _get_hour_range(self) -> list:
        active_hours = os.getenv("ACTIVE_HOURS")
        if active_hours is None:
            active_hours = DEFAULT_ACTIVE_HOURS
        start_str, end_str = active_hours.split("-")
        start = datetime.datetime.strptime(start_str, "%H:%M").time()
        end = datetime.datetime.strptime(end_str, "%H:%M").time()
        if start > end:
            raise ValueError("Start time must be before end time, got start: {start}, end: {end}")
        return start, end

    def addHandle(self, notificationHandle: NotificationHandle) -> None:
        self.__handleList.append(notificationHandle)

    #####
    ######################################################



    def fetch_l1_visa_stats(self, year_month: str):
        """
        Fetch L1 Visa pass rate, waiting days, and all L1 case details for a given year_month (format: YYYYMM).
        Returns (pass_rate, waiting_days, l1_cases) where l1_cases is a list of dicts for each L1 case.
        """

        # The dispdate param is in format YYYY-MM
        url = f"https://www.checkee.info/main.php?dispdate={year_month[:4]}-{year_month[4:]}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
    
        # Find the detailed case table (with Update, ID, Visa Type, etc.)
        pass_rate = "N/A"
        waiting_days = []
        l1_cases = []
        tables = soup.find_all('table')
        l1_clear = 0
        l1_reject = 0
        waiting_days_list = []
        for table in tables:
            header = table.find('tr')
            if not header:
                continue
            header_cells = [td.get_text(strip=True) for td in header.find_all('td')]
            # Look for the correct table by header
            if (
                    'Visa Type' in header_cells and
                    'Status' in header_cells and
                    'Waiting Day(s)' in header_cells
            ):
                # Find column indices for all columns
                col_indices = {col: i for i, col in enumerate(header_cells)}
                visa_type_idx = col_indices['Visa Type']
                status_idx = col_indices['Status']
                waiting_idx = col_indices['Waiting Day(s)']
                # Iterate over all rows (skip header)
                for row in table.find_all('tr')[1:]:
                    cols = row.find_all('td')
                    if len(cols) <= max(visa_type_idx, status_idx, waiting_idx):
                        continue
                    visa_type = cols[visa_type_idx].get_text(strip=True)
                    status = cols[status_idx].get_text(strip=True)
                    waiting = cols[waiting_idx].get_text(strip=True)
                    if visa_type == 'L1':
                        # Collect all columns for this row
                        case = {col: cols[idx].get_text(strip=True) if idx < len(cols) else '' for col, idx in
                                col_indices.items()}
                        l1_cases.append(case)
                        if status == 'Clear':
                            l1_clear += 1
                            try:
                                waiting_days_list.append(int(waiting))
                            except Exception:
                                pass
                        else:
                            l1_reject += 1
                break
        total = l1_clear + l1_reject
        if total > 0:
            pass_rate = f"{l1_clear / total:.2%}"
        if waiting_days_list:
            waiting_days = waiting_days_list
        return (pass_rate, waiting_days, l1_cases)


    def get_l1_visa_stats_for_months(self, months: list):
        results = {}
        for ym in months:
            pass_rate, waiting_days, l1_cases = self.fetch_l1_visa_stats(ym)
            results[ym] = {
                'pass_rate': pass_rate,
                'waiting_days': waiting_days,
                'l1_cases': l1_cases
            }
        return results


    def fetch_recent_completed_cases(self, dispdate, days):
        """
        Fetch cases completed in the last `days` from the given dispdate (YYYY-MM-DD or YYYY-MM).
        Returns (count, waiting_days_list, all_cases_table)
        """
        url = f"https://www.checkee.info/main.php?dispdate={dispdate}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        tables = soup.find_all('table')
        all_cases = []
        completed_cases = []
        waiting_days_list = []
        from datetime import datetime, timedelta
        # Try to parse dispdate as YYYY-MM-DD, fallback to YYYY-MM
        try:
            base_date = datetime.strptime(dispdate, "%Y-%m-%d")
        except Exception:
            base_date = None
        for table in tables:
            header = table.find('tr')
            if not header:
                continue
            header_cells = [td.get_text(strip=True) for td in header.find_all('td')]
            if (
                    'Visa Type' in header_cells and
                    'Status' in header_cells and
                    'Waiting Day(s)' in header_cells and
                    'Complete Date' in header_cells
            ):
                col_indices = {col: i for i, col in enumerate(header_cells)}
                complete_idx = col_indices['Complete Date']
                waiting_idx = col_indices['Waiting Day(s)']
                status_idx = col_indices['Status']
                # Collect all cases
                for row in table.find_all('tr')[1:]:
                    cols = row.find_all('td')
                    if len(cols) < len(header_cells):
                        continue
                    case = {col: cols[idx].get_text(strip=True) if idx < len(cols) else '' for col, idx in
                            col_indices.items()}
                    all_cases.append(case)
                    # Only consider completed cases (status == 'Clear')
                    if case['Status'] == 'Clear' and case['Complete Date'] and base_date:
                        try:
                            comp_date = datetime.strptime(case['Complete Date'], "%Y-%m-%d")
                            days_diff = (comp_date - base_date).days
                            if 0 <= days_diff <= days - 1:
                                completed_cases.append(case)
                                try:
                                    waiting_days_list.append(int(case['Waiting Day(s)']))
                                except Exception:
                                    pass
                        except Exception:
                            pass
                break
        return len(completed_cases), waiting_days_list, all_cases

    #####
    def send(self) -> None:
        res = query_status(
            self.__location,
            self.__number,
            self.__passport_number,
            self.__surname,
            self.__captchaHandle,
        )
        print(res)
        current_status = res["status"]
        current_last_updated = res["case_last_updated"]
        print(f"Current status: {current_status} - Last updated: {current_last_updated}")
        # Load the previous statuses from the file
        statuses = self.__load_statuses()
        
        # Check if the current status is different from the last recorded status
        # if not statuses or current_status != statuses[-1].get("status", None) or current_last_updated != statuses[-1].get("last_updated", None):
        #     self.__save_current_status(current_status, current_last_updated)
        #     self.__send_notifications(res)
        # else:
        #     print("Status unchanged. No notification sent.")
        dispdate = "2026-01-19"
    
        count_3, waiting_days_list_3, all_cases = self.fetch_recent_completed_cases(dispdate, 3)
        count_3, waiting_days_list_3, all_cases = self.fetch_recent_completed_cases(dispdate, 3)
        count_1, waiting_days_list_1, _ = self.fetch_recent_completed_cases(dispdate, 1)
        report_lines = []
        report_lines.append(f"Completed cases in last 1 day: {count_1}")
        report_lines.append(f"Completed cases in last 3 days: {count_3}")
        report_lines.append(f"Waiting days for these cases (3 days): {waiting_days_list_3}")
        # Remove 'Update' column if present
        all_cases_no_update = []
        for case in all_cases:
            case_no_update = {k: v for k, v in case.items() if k != 'Update'}
            all_cases_no_update.append(case_no_update)
        report_lines.append("All cases table:")
        if all_cases_no_update:
            report_lines.append(tabulate(all_cases_no_update, headers="keys", tablefmt="grid", showindex=False))
        else:
            report_lines.append("No cases found.")
    
        months = ["202510", "202511", "202512"]
        stats = self.get_l1_visa_stats_for_months(months)
        for ym, data in stats.items():
            report_lines.append(f"Month: {ym}")
            report_lines.append(f"  L1 Pass Rate: {data['pass_rate']}")
            report_lines.append(f"  Waiting Days: {data['waiting_days']}")
            report_lines.append(f"  L1 Cases Table:")
            if data['l1_cases']:
                l1_cases_no_update = []
                for case in data['l1_cases']:
                    case_no_update = {k: v for k, v in case.items() if k != 'Update'}
                    l1_cases_no_update.append(case_no_update)
                report_lines.append(tabulate(l1_cases_no_update, headers="keys", tablefmt="grid", showindex=False))
            else:
                report_lines.append("  No L1 cases found.")
            report_lines.append("")
    
        # Print to console as before
        for line in report_lines:
            print(line)

        res['reportlines'] = report_lines
        self.__send_notifications(res)
    def __load_statuses(self) -> list:
        if os.path.exists(self.__status_file):
            with open(self.__status_file, "r") as file:
                return json.load(file).get("statuses", [])
        return []

    def __save_current_status(self, status: str, last_updated: str) -> None:
        statuses = self.__load_statuses()
        statuses.append({
            "status": status,
            "last_updated": last_updated,
            "date": datetime.datetime.now().isoformat()
        })

        with open(self.__status_file, "w") as file:
            json.dump({"statuses": statuses}, file)

    def __send_notifications(self, res: dict) -> None:
        if res["status"] == "Refused":
            try:
                TIMEZONE = os.environ["TIMEZONE"]
                localTimeZone = pytz.timezone(TIMEZONE)
                localTime = datetime.datetime.now(localTimeZone)
            except pytz.exceptions.UnknownTimeZoneError:
                print("UNKNOWN TIMEZONE Error, use default")
                localTime = datetime.datetime.now()
            except KeyError:
                print("TIMEZONE Error")
                localTime = datetime.datetime.now()

            active_hour_start, active_hour_end = self._get_hour_range()
            start_dt = datetime.datetime.combine(localTime.date(), active_hour_start, tzinfo=localTimeZone)
            end_dt = datetime.datetime.combine(localTime.date(), active_hour_end, tzinfo=localTimeZone)
            if not (start_dt <= localTime <= end_dt):
                print(
                    f"Outside active hours {os.getenv('ACTIVE_HOURS', DEFAULT_ACTIVE_HOURS)}. "
                    "No notification sent for Refused status."
                )
                return

        for notificationHandle in self.__handleList:
            notificationHandle.send(res)
