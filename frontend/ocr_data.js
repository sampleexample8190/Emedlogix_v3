// Sample OCR Data - Using Standardized Schemas
// This represents what your friend's OCR engine should return

const ocrData = {
    // Document 1: State Medical License (Standard Schema)
    license: {
        "document_type": "state_medical_license",
        "license_number": "290388",
        "provider_name": "SACHIN KUMAR AMRUTHLAL JAIN",
        "first_name": "SACHIN",
        "last_name": "KUMAR AMRUTHLAL JAIN",
        "date_of_birth": "01/19/1981",
        "gender": "M",
        "license_status": "ACTIVE",
        "issue_date": "01/01/2021",
        "expiration_date": "12/31/2022",
        "address": {
            "street": "19 BOULDER RIDGE ROAD",
            "city": "SCARSDALE",
            "state": "NY",
            "zip_code": "10583",
            "country": "USA"
        },
        "contact": {
            "phone_number": "2489611758",
            "email": "jaincardiovascular@gmail.com"
        }
    },

    // Document 2: Tax ID / IRS Letter (Standard Schema)
    tax: {
        "document_type": "tax_id",
        "ein": "82-5443276",
        "provider_name": "SACHIN KUMAR AMRUTHLAL JAIN",
        "first_name": "SACHIN",
        "last_name": "KUMAR AMRUTHLAL JAIN",
        "business_name": "CARDIOVASCULAR PC",
        "issue_date": "05-04-2018",
        "form_type": "CP 575 A",
        "address": {
            "street": "401 E 80TH ST APT 26A",
            "city": "NEW YORK",
            "state": "NY",
            "zip_code": "10075"
        }
    },

    // Document 3: Federal DEA Certificate (Standard Schema)
    dea: {
        "document_type": "dea_certificate",
        "dea_number": "FA3192577",
        "provider_name": "SACHIN KUMAR AMRUTHAL JAIN",
        "first_name": "SACHIN",
        "last_name": "KUMAR AMRUTHAL JAIN",
        "business_activity": "PRACTITIONER",
        "issue_date": "05-03-2021",
        "expiration_date": "06-30-2024",
        "schedules": ["2", "2N", "3", "3N", "4", "5"],
        "fee_paid": "$888",
        "address": {
            "street": "944 N BROADWAY, STE #202",
            "city": "YONKERS",
            "state": "NY",
            "zip_code": "10701"
        }
    },

    // Document 4: Malpractice Insurance (Standard Schema)
    malpractice: {
        "document_type": "malpractice_insurance",
        "policy_number": "RMD313853",
        "provider_name": "SACHIN KUMAR AMRUTHLAL JAIN",
        "first_name": "SACHIN",
        "last_name": "KUMAR AMRUTHLAL JAIN",
        "insurer_name": "Med Pro RRG",
        "effective_date": "05/07/2025",
        "expiration_date": "05/07/2026",
        "specialty": "Interventional Cardiology",
        "coverage_limits": {
            "per_claim": "$2,300,000",
            "aggregate": "$6,900,000"
        },
        "address": {
            "street": "19 BOULDER RIDGE ROAD",
            "city": "SCARSDALE",
            "state": "NY",
            "zip_code": "10583"
        }
    },

    // Document 5: Board Certification (Standard Schema)
    board: {
        "document_type": "board_certification",
        "provider_name": "SACHIN KUMAR AMRUTHLAL JAIN",
        "first_name": "SACHIN",
        "last_name": "KUMAR AMRUTHLAL JAIN",
        "certification_type": "Interventional Cardiology",
        "certifying_board": "ABIM",
        "certification_id": "323584",
        "issue_date": "01/01/2025",
        "expiration_date": "12/31/2029",
        "status": "Certified, Participating in MOC",
        "specialty_code": "207RC0000X"
    }
};
