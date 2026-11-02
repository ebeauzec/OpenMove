# Legal Notice & Intellectual Property Declaration

> This document accompanies the [LICENSE](LICENSE) file and gives the legal and intellectual property context for **OpenMove**. Both documents together are the complete terms governing this Software. If they conflict, the `LICENSE` file prevails.

---

## 1. Ownership and Intellectual Property Rights

OpenMove, including its source code, object code, documentation, specifications, architecture, designs, workflows, configurations, scripts, build materials, user interfaces and all related materials, whether existing now or created in the future, is the sole and exclusive intellectual property of **Eugene Beauzec** and **Obi1 - FZCO** (together, the "Author").

All rights, title and interest in the Software, including copyright, economic rights, moral rights to the extent applicable, database rights, know-how, trade secrets, inventions, improvements, derivative works and updates, are and remain vested in the Author, unless expressly transferred by the Author in a separate written agreement signed by the Author.

The Software was independently conceived, authored, developed, tested and assembled by the Author on the Author's own time and with independent tools and resources. It is not a work-for-hire, commissioned work, employment deliverable, client assignment, internal project or sponsored project of any employer, former employer, client, sponsor, platform provider, user, contributor or third party. None of them acquires any ownership interest, licence, royalty, claim or other right in the Software through an engagement with the Author, access to or use of the Software, feedback, or contribution.

The Software does not contain, derive from or rely on any confidential information, proprietary material, customer data, trade secrets, private repositories, internal systems, credentials, unpublished documentation or non-public information belonging to any third party.

All rights not expressly granted in writing are reserved. No person may copy, reproduce, modify, adapt, translate, publish, distribute, commercialise, sublicense, sell, assign, transfer, pledge, reverse engineer, remove attribution from, or claim authorship or ownership of the Software, in whole or in part, except as authorised in writing by the Author.

If third-party proprietary material is credibly identified as inadvertently included, the Author may remove, replace or remediate it promptly, without admission of liability and without prejudice to ownership of the rest of the Software.

Copyright © 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.

## 2. How Ownership Is Kept

| Measure | Where |
|---|---|
| Copyright notice at the top of every source file | Enforced by `tools/guard_sensitive.py` at commit and push. |
| Single author in the version history | Enforced by the pre-push hook (`.githooks/pre-push`) and the guard. |
| No outside contributions without a signed assignment | `LICENSE`, "Intellectual Property Reservation". |
| No use of the Software to train or evaluate machine-learning models | `LICENSE`, "Restrictions". |
| Licence, legal notice and third-party notices ship with every copy | Section 5 of this document. |
| No third-party or cloud data in the repository | `docs/DEVELOPMENT.md`, "Rules"; the guard. |

## 3. Third-Party Product References and Trademarks

References to third-party products, services, companies, platforms or technologies are for identification, compatibility and documentation only. They do not imply affiliation, sponsorship, endorsement or any commercial relationship. All third-party names remain the property of their owners.

**OpenStack®** and the OpenStack logo are registered trademarks of the OpenInfra Foundation. Cinder, Keystone, Nova and Glance are OpenStack project names. OpenMove is an independent tool and is not affiliated with, endorsed by or sponsored by the OpenInfra Foundation or the OpenStack project. No customer data of any kind is embedded in, distributed with, or derivable from the Software.

## 4. License Summary

| Use case | Permission |
|---|---|
| Personal, educational or research use | Free, no approval required |
| Non-commercial organisational use (internal, no revenue) | Free, no approval required |
| Sharing with colleagues (same organisation, non-commercial, no charge) | Permitted |
| Modification for personal non-commercial use | Permitted |
| Commercial use (any revenue-generating or cost-saving context) | Requires the Author's **prior written consent** |
| Redistribution (source or compiled) | Requires the Author's **prior written consent** |
| Derivative works distributed to third parties | Requires the Author's **prior written consent** |
| Training or evaluating a machine-learning model on the Software | Prohibited |
| Claiming authorship or ownership | Prohibited |
| Removing copyright or attribution notices | Prohibited |
| Representing as an official OpenStack or OpenInfra product | Prohibited |

See [LICENSE](LICENSE) for the full terms.

## 5. Attribution Requirement

Any permitted use, deployment or sharing of the Software must keep:

1. This `LEGAL.md`, intact and unmodified;
2. The `LICENSE` file, intact and unmodified;
3. The notice `Copyright © 2026 Eugene Beauzec and Obi1 - FZCO` wherever the Software or its output is displayed or distributed.

## 6. Commercial Licensing and Contact

Commercial use, integration into a product or service, a managed-service offering, or distribution to third parties needs a commercial licence from the Author **before** it starts. Retroactive licensing is not available. Contact the Author through the repository's issue tracker or the contact details in the project documentation.

## 7. No Warranty, Limitation of Liability

The Software is provided as is. The Author gives no warranty on the accuracy, completeness or fitness for purpose of any data, report or recommendation it produces. OpenMove can change a cloud (migration, retype, detach). Users must verify every planned change before it runs on a production system and are solely responsible for the result. The Author is not liable for loss, damage, outage, data loss or financial harm arising from use of, or reliance on, the Software.

## 8. Data Policy

OpenMove is distributed as source code only. Data read from an OpenStack cloud is retrieved by each user, with their own credentials, onto their own machine, and is stored locally (the git-ignored `data/` folder). It is not redistributed with the Software and must not be committed to the repository.

## 9. Governing Provisions

- This document and `LICENSE` are the complete legal terms for the Software.
- If a provision is unenforceable, the rest stays in force.
- Failure to enforce a provision is not a waiver of later enforcement.

*Last updated: 2026-11-02*
*Copyright © 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.*
